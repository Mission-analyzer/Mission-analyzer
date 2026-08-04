"""
analyzer.py — ядро анализа миссии: проверки высоты/углов поворота,
профиль высоты для графика. Никакой отрисовки и никакого GUI —
это только логика и данные. Рисованием занимаются elevation_view.py
и map_view.py, оболочкой — app.py.
"""

from __future__ import annotations

import csv
import math

from waypoints import Waypoint, LAND_COMMANDS, DO_CHANGE_SPEED, command_name
from geo import haversine_m, vertex_angle_deg, bearing_deg
from srtm import SRTMTerrain, SRTMError
import i18n

ALT_MIN_M = 40.0
TURN_ANGLE_MIN_DEG = 2.0
FLIGHT_PATH_ANGLE_MAX_DEG = 2.0


class MissionAnalyzer:
    def __init__(
        self,
        waypoints: list[Waypoint],
        alt_min: float = ALT_MIN_M,
        turn_min: float = TURN_ANGLE_MIN_DEG,
        angle_max: float = FLIGHT_PATH_ANGLE_MAX_DEG,
        terrain: SRTMTerrain | None = None,
        home_amsl: float | None = None,
    ):
        self.all_wps = waypoints
        self.nav_wps = [wp for wp in waypoints if wp.is_nav_point]
        # для проверки высоты берём отдельный список: сюда попадает TAKEOFF,
        # даже если у него нет координат (lat=lon=0, это нормально для взлёта),
        # но не попадает LAND (там высота 0 — норма, а не ошибка)
        self.alt_wps = [wp for wp in waypoints if wp.is_altitude_point]
        self.alt_min = alt_min
        self.turn_min = turn_min
        self.angle_max = angle_max
        self.terrain = terrain
        # если home_amsl не задан явно — берём высоту точки home (первая
        # точка миссии, index 0) как приближение AMSL точки взлёта
        self.home_amsl = home_amsl
        if self.home_amsl is None and self.nav_wps:
            self.home_amsl = self.nav_wps[0].alt
        self.issues: list[dict] = []

    # --- проверки ---

    def check_altitude(self):
        for wp in self.alt_wps:
            if wp.alt < self.alt_min:
                self.issues.append({
                    "type": "LOW_ALTITUDE",
                    "wp_index": wp.index,
                    "value": round(wp.alt, 1),
                    "threshold": self.alt_min,
                })

    def check_turn_angles(self):
        pts = self.nav_wps
        for i in range(1, len(pts) - 1):
            a, b, c = pts[i - 1], pts[i], pts[i + 1]
            ang = vertex_angle_deg(a, b, c)
            if math.isnan(ang):
                continue
            if ang < self.turn_min:
                self.issues.append({
                    "type": "SHARP_TURN",
                    "wp_index": b.index,
                    "value": round(ang, 2),
                    "threshold": self.turn_min,
                })

    def _absolute_alt(self, wp: Waypoint) -> float | None:
        """AMSL-высота точки. Для frame=10 (terrain) нужен self.terrain."""
        if wp.frame in (0, 2):
            return wp.alt
        if wp.frame == 10:
            if self.terrain is None:
                return None
            try:
                return self.terrain.get_elevation(wp.lat, wp.lon) + wp.alt
            except SRTMError:
                return None
        # frame 3 и большинство остальных — относительно home
        return (self.home_amsl or 0.0) + wp.alt

    def check_altitude_agl(self):
        """
        Реальная высота над рельефом (AGL) с учётом SRTM. В отличие от
        check_altitude() (которая просто смотрит на число в файле),
        здесь высота точки приводится к AMSL и из неё вычитается высота
        земли под этой конкретной точкой.
        """
        if self.terrain is None:
            return

        for wp in self.alt_wps:
            if not wp.has_position:
                # например TAKEOFF без заданных координат — нет позиции,
                # рельеф под ней определить нельзя, пропускаем
                continue

            if wp.frame == 10:
                agl = wp.alt
            else:
                abs_alt = self._absolute_alt(wp)
                try:
                    ground = self.terrain.get_elevation(wp.lat, wp.lon)
                except SRTMError:
                    self.issues.append({
                        "type": "SRTM_MISSING",
                        "wp_index": wp.index,
                        "value": None,
                        "threshold": None,
                        "extra": {"lat": wp.lat, "lon": wp.lon},
                    })
                    continue
                agl = abs_alt - ground

            if agl < self.alt_min:
                self.issues.append({
                    "type": "LOW_AGL",
                    "wp_index": wp.index,
                    "value": round(agl, 1),
                    "threshold": self.alt_min,
                })

    def check_flight_path_angle(self):
        """
        Угол наклона траектории (набор/снижение) между соседними точками:
        atan2(изменение высоты, горизонтальная дистанция), в градусах.
        Положительный — набор высоты, отрицательный — снижение.
        Критично, если |угол| > self.angle_max.
        """
        pts = self.nav_wps
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            alt_a, alt_b = self._absolute_alt(a), self._absolute_alt(b)
            if alt_a is None or alt_b is None:
                continue
            dist = haversine_m(a.lat, a.lon, b.lat, b.lon)
            if dist < 1e-6:
                continue
            angle = math.degrees(math.atan2(alt_b - alt_a, dist))
            if abs(angle) > self.angle_max:
                self.issues.append({
                    "type": "STEEP_ANGLE",
                    "wp_index": b.index,
                    "value": round(angle, 2),
                    "threshold": self.angle_max,
                    "extra": {"from_idx": a.index},
                })

    def check_altitude_agl_segments(self, step_m: float = 50.0):
        """
        AGL не только В точках маршрута, но и ВДОЛЬ прямой линии между ними.
        Высота миссии между двумя точками интерполируется линейно (это то,
        как реально летит планер), а рельеф под этой линией может быть не
        монотонным — например, холм ровно посередине безопасного по обеим
        точкам сегмента. check_altitude_agl() такое не ловит, потому что
        смотрит только на сами точки. Здесь — сэмплируем сегмент с шагом
        step_m и берём худший (минимальный) клиренс по всей его длине.
        """
        if self.terrain is None:
            return

        pts = self.nav_wps
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            alt_a, alt_b = self._absolute_alt(a), self._absolute_alt(b)
            if alt_a is None or alt_b is None:
                continue

            seg_dist = haversine_m(a.lat, a.lon, b.lat, b.lon)
            n_steps = max(1, int(seg_dist // step_m))

            worst_clearance = None
            for s in range(n_steps + 1):
                frac = s / n_steps
                lat = a.lat + (b.lat - a.lat) * frac
                lon = a.lon + (b.lon - a.lon) * frac
                mission_alt = alt_a + (alt_b - alt_a) * frac
                try:
                    ground = self.terrain.get_elevation(lat, lon)
                except SRTMError:
                    continue
                clearance = mission_alt - ground
                if worst_clearance is None or clearance < worst_clearance:
                    worst_clearance = clearance

            if worst_clearance is not None and worst_clearance < self.alt_min:
                self.issues.append({
                    "type": "LOW_AGL_SEGMENT",
                    "wp_index": b.index,
                    "value": round(worst_clearance, 1),
                    "threshold": self.alt_min,
                    "extra": {"from_idx": a.index, "to_idx": b.index},
                })

    # чтобы добавить новую метрику: допиши def check_xxx(self): ... и
    # вызови её из analyze()

    def analyze(self) -> list[dict]:
        self.issues.clear()
        self.check_altitude()
        self.check_turn_angles()
        self.check_altitude_agl()
        self.check_altitude_agl_segments()
        self.check_flight_path_angle()
        return self.issues

    def _landing_approach_indices(self, n_legs: int = 3) -> tuple[int, int] | None:
        """
        Ищет последнюю точку LAND в маршруте (если её нет — берёт последнюю
        точку маршрута) и возвращает (start_i, end_i) — индексы в self.nav_wps
        для последних n_legs отрезков захода. None, если точек мало.
        """
        pts = self.nav_wps
        if len(pts) < 2:
            return None

        land_idx_in_pts = None
        for i in range(len(pts) - 1, -1, -1):
            if pts[i].command in LAND_COMMANDS:
                land_idx_in_pts = i
                break
        end_i = land_idx_in_pts if land_idx_in_pts is not None else len(pts) - 1

        start_i = max(0, end_i - n_legs)
        if start_i == end_i:
            return None
        return start_i, end_i

    def landing_approach_profile(self, n_legs: int = 3) -> list[dict] | None:
        """
        Глиссада захода на посадку: последние n_legs отрезков маршрута,
        заканчивающиеся на фактической точке LAND в миссии (если её нет —
        на последней точке маршрута). Для каждого отрезка: дистанция,
        азимут (истинный курс) и угол снижения.

        Возвращает None, если в маршруте меньше 2 точек с позицией.
        """
        idxs = self._landing_approach_indices(n_legs)
        if idxs is None:
            return None
        start_i, end_i = idxs
        pts = self.nav_wps

        legs = []
        for i in range(start_i, end_i):
            a, b = pts[i], pts[i + 1]
            dist = haversine_m(a.lat, a.lon, b.lat, b.lon)
            brg = bearing_deg(a.lat, a.lon, b.lat, b.lon)

            alt_a, alt_b = self._absolute_alt(a), self._absolute_alt(b)
            angle = None
            if alt_a is not None and alt_b is not None:
                if dist < 1e-6:
                    angle = 90.0 if alt_b > alt_a else (-90.0 if alt_b < alt_a else 0.0)
                else:
                    angle = math.degrees(math.atan2(alt_b - alt_a, dist))

            legs.append({
                "from_idx": a.index,
                "to_idx": b.index,
                # "сквозная" нумерация — какая по счёту это точка среди
                # РЕАЛЬНЫХ точек маршрута (без учёта команд без координат,
                # которые сдвигают номер строки в файле, но не являются
                # точками сами по себе)
                "from_seq": i + 1,
                "to_seq": i + 2,
                "distance_m": dist,
                "bearing_deg": brg,
                "angle_deg": angle,
                "is_land": b.command in LAND_COMMANDS,
            })

        return legs

    def landing_approach_elevation_profile(self, n_legs: int = 3, step_m: float = 20.0) -> dict | None:
        """
        Мелкая выборка (шаг step_m, по умолчанию гуще, чем в основном
        elevation_profile, — участок короткий, есть смысл в детальности)
        вдоль последних n_legs отрезков перед посадкой: дистанция, высота
        миссии (интерполяция) и высота рельефа. Для отрисовки заливки
        рельефа под глиссадой на графике.

        Возвращает None, если точек мало.
        """
        idxs = self._landing_approach_indices(n_legs)
        if idxs is None:
            return None
        start_i, end_i = idxs
        pts = self.nav_wps[start_i:end_i + 1]
        if len(pts) < 2:
            return None

        abs_alts = [self._absolute_alt(wp) for wp in pts]

        dist_list: list[float] = []
        mission_alt_list: list[float | None] = []
        terrain_alt_list: list[float | None] = []

        cum = 0.0
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            seg_dist = haversine_m(a.lat, a.lon, b.lat, b.lon)
            n_steps = max(1, int(seg_dist // step_m))
            alt_a, alt_b = abs_alts[i], abs_alts[i + 1]

            for s in range(n_steps):
                frac = s / n_steps
                lat = a.lat + (b.lat - a.lat) * frac
                lon = a.lon + (b.lon - a.lon) * frac
                dist_list.append(cum + seg_dist * frac)
                if alt_a is not None and alt_b is not None:
                    mission_alt_list.append(alt_a + (alt_b - alt_a) * frac)
                else:
                    mission_alt_list.append(None)
                if self.terrain is not None:
                    try:
                        terrain_alt_list.append(self.terrain.get_elevation(lat, lon))
                    except SRTMError:
                        terrain_alt_list.append(None)
                else:
                    terrain_alt_list.append(None)

            cum += seg_dist

        last = pts[-1]
        dist_list.append(cum)
        mission_alt_list.append(abs_alts[-1])
        if self.terrain is not None:
            try:
                terrain_alt_list.append(self.terrain.get_elevation(last.lat, last.lon))
            except SRTMError:
                terrain_alt_list.append(None)
        else:
            terrain_alt_list.append(None)

        return {"dist": dist_list, "mission_alt": mission_alt_list, "terrain_alt": terrain_alt_list}

    def landing_approach_points(self, n_legs: int = 3) -> list[dict] | None:
        """
        Точки захода на посадку (для графика): накопительная дистанция (м,
        от первой точки захода) и абсолютная высота каждой точки.
        """
        idxs = self._landing_approach_indices(n_legs)
        if idxs is None:
            return None
        start_i, end_i = idxs
        pts = self.nav_wps

        result = []
        cum = 0.0
        prev = None
        for i in range(start_i, end_i + 1):
            wp = pts[i]
            if prev is not None:
                cum += haversine_m(prev.lat, prev.lon, wp.lat, wp.lon)
            result.append({
                "idx": wp.index,
                "seq": i + 1,  # сквозной номер среди реальных точек маршрута
                "dist": cum,
                "alt": self._absolute_alt(wp),
                "is_land": wp.command in LAND_COMMANDS,
                "command_name": command_name(wp.command),
            })
            prev = wp

        return result

    def landing_approach_speed_markers(self, n_legs: int = 3) -> list[dict]:
        """
        Команды DO_CHANGE_SPEED, сработавшие в пределах участка захода на
        посадку: для каждой такой команды ищем ПРЕДЫДУЩУЮ реальную точку
        маршрута в этом же диапазоне (после неё команда и срабатывает) —
        у самой DO_CHANGE_SPEED координат нет, поэтому её показывать не на
        чем, а вот предыдущая точка вполне реальна и есть на графике.
        """
        idxs = self._landing_approach_indices(n_legs)
        if idxs is None:
            return []
        start_i, end_i = idxs
        pts = self.nav_wps
        approach_pts = pts[start_i:end_i + 1]
        start_wp_index = approach_pts[0].index
        end_wp_index = approach_pts[-1].index

        markers = []
        for wp in self.all_wps:
            if wp.command != DO_CHANGE_SPEED:
                continue
            if not (start_wp_index <= wp.index <= end_wp_index):
                continue

            preceding = approach_pts[0]
            preceding_seq = start_i + 1
            for offset, p in enumerate(approach_pts):
                if p.index <= wp.index:
                    preceding = p
                    preceding_seq = start_i + offset + 1
                else:
                    break

            markers.append({
                "after_wp_index": preceding.index,
                "after_wp_seq": preceding_seq,
                "speed": wp.param2,
                "speed_type": int(wp.param1),
                "command_name": command_name(wp.command),
            })
        return markers

    def flight_path_angle_profile(self) -> list[dict]:
        """
        Угол наклона траектории по сегментам маршрута — для графика.
        Каждый элемент: dist_start/dist_end (м, накопительно), angle (град.
        или None, если высоту одной из точек посчитать не удалось),
        from_idx/to_idx — индексы точек сегмента.
        """
        pts = self.nav_wps
        if len(pts) < 2:
            raise ValueError("Недостаточно точек маршрута для профиля (нужно минимум 2)")

        segments = []
        cum = 0.0
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            dist = haversine_m(a.lat, a.lon, b.lat, b.lon)
            alt_a, alt_b = self._absolute_alt(a), self._absolute_alt(b)

            angle = None
            if alt_a is not None and alt_b is not None:
                if dist < 1e-6:
                    angle = 90.0 if alt_b > alt_a else (-90.0 if alt_b < alt_a else 0.0)
                else:
                    angle = math.degrees(math.atan2(alt_b - alt_a, dist))

            segments.append({
                "dist_start": cum,
                "dist_end": cum + dist,
                "angle": angle,
                "from_idx": a.index,
                "to_idx": b.index,
                "from_seq": i + 1,
                "to_seq": i + 2,
            })
            cum += dist

        return segments

    def total_distance_m(self) -> float:
        pts = self.nav_wps
        return sum(
            haversine_m(pts[i].lat, pts[i].lon, pts[i + 1].lat, pts[i + 1].lon)
            for i in range(len(pts) - 1)
        )

    def elevation_profile(self, step_m: float = 50.0) -> dict:
        """
        Сэмплирует маршрут с шагом step_m и возвращает профиль:
        дистанцию, высоту миссии (AMSL, линейная интерполяция между
        точками) и высоту рельефа (если задан self.terrain).
        Используется elevation_view.py для отрисовки графика.
        """
        pts = self.nav_wps
        if len(pts) < 2:
            raise ValueError("Недостаточно точек маршрута для профиля (нужно минимум 2)")

        abs_alts = [self._absolute_alt(wp) for wp in pts]

        dist_list: list[float] = []
        mission_alt_list: list[float | None] = []
        terrain_alt_list: list[float | None] = []
        wp_markers: list[tuple[float, float | None, int, int]] = []

        cum_dist = 0.0
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            seg_dist = haversine_m(a.lat, a.lon, b.lat, b.lon)
            n_steps = max(1, int(seg_dist // step_m))
            alt_a, alt_b = abs_alts[i], abs_alts[i + 1]

            wp_markers.append((cum_dist, alt_a, a.index, i + 1))

            for s in range(n_steps):
                frac = s / n_steps
                lat = a.lat + (b.lat - a.lat) * frac
                lon = a.lon + (b.lon - a.lon) * frac
                dist_list.append(cum_dist + seg_dist * frac)
                if alt_a is not None and alt_b is not None:
                    mission_alt_list.append(alt_a + (alt_b - alt_a) * frac)
                else:
                    mission_alt_list.append(None)
                if self.terrain is not None:
                    try:
                        terrain_alt_list.append(self.terrain.get_elevation(lat, lon))
                    except SRTMError:
                        terrain_alt_list.append(None)
                else:
                    terrain_alt_list.append(None)

            cum_dist += seg_dist

        # финальная точка маршрута
        last = pts[-1]
        dist_list.append(cum_dist)
        mission_alt_list.append(abs_alts[-1])
        if self.terrain is not None:
            try:
                terrain_alt_list.append(self.terrain.get_elevation(last.lat, last.lon))
            except SRTMError:
                terrain_alt_list.append(None)
        else:
            terrain_alt_list.append(None)
        wp_markers.append((cum_dist, abs_alts[-1], last.index, len(pts)))

        return {
            "dist": dist_list,
            "mission_alt": mission_alt_list,
            "terrain_alt": terrain_alt_list,
            "waypoints": wp_markers,
        }

    # --- вывод ---

    def print_report(self):
        print(i18n.t("report_nav_points", n=len(self.nav_wps)))
        print(i18n.t("report_total_distance", km=self.total_distance_m() / 1000))

        land_pts = [wp for wp in self.all_wps if wp.command in LAND_COMMANDS]
        no_pos_alt_pts = [wp for wp in self.alt_wps if not wp.has_position]
        notes = []
        if land_pts:
            idxs = ", ".join(str(wp.index) for wp in land_pts)
            notes.append(i18n.t("report_note_land", idxs=idxs))
        if no_pos_alt_pts:
            idxs = ", ".join(str(wp.index) for wp in no_pos_alt_pts)
            notes.append(i18n.t("report_note_no_pos", idxs=idxs))
        if notes:
            print("(" + "; ".join(notes) + ")")
        print()

        legs = self.landing_approach_profile()
        if legs:
            print(i18n.t("report_landing_approach_title"))
            for leg in legs:
                dist = leg["distance_m"]
                brg = leg["bearing_deg"]
                ang = leg["angle_deg"]
                ang_str = f"{ang:+.1f}°" if ang is not None else "—"
                print(i18n.t(
                    "report_landing_leg_line",
                    from_idx=leg["from_seq"], to_idx=leg["to_seq"],
                    dist=dist, bearing=brg, angle=ang_str,
                ))
            for marker in self.landing_approach_speed_markers():
                print(i18n.t(
                    "report_landing_speed_line",
                    wp_index=marker["after_wp_seq"], speed=marker["speed"],
                    speed_type=i18n.speed_type_label(marker["speed_type"]),
                ))
            print()

        if not self.issues:
            print(i18n.t("report_no_critical"))
            return

        by_type: dict[str, list[dict]] = {}
        for issue in self.issues:
            by_type.setdefault(issue["type"], []).append(issue)

        for kind, items in by_type.items():
            threshold = items[0].get("threshold")
            title = i18n.issue_title(kind, threshold)
            count = i18n.t("report_count_suffix", count=len(items))
            print(f"=== {title}: {count} ===")
            for it in items:
                detail = i18n.format_issue_detail(it)
                print(i18n.t("report_wp_line", idx=it["wp_index"], detail=detail))
            print()

    def export_csv(self, out_path: str):
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f, fieldnames=["type", "wp_index", "value", "threshold", "detail"]
            )
            writer.writeheader()
            for issue in self.issues:
                row = {k: issue.get(k) for k in ("type", "wp_index", "value", "threshold")}
                row["detail"] = i18n.format_issue_detail(issue)
                writer.writerow(row)
