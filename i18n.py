"""
i18n.py — локализация интерфейса: украинский и английский (без русского —
это язык только переписки с разработчиком, не язык программы).

Простое глобальное текущее состояние языка (get_lang/set_lang) — это
однопользовательское десктопное приложение с одним окном, глобального
состояния здесь достаточно и не создаёт проблем.
"""

from __future__ import annotations

LANGS = ("uk", "en")
DEFAULT_LANG = "uk"

_current_lang = DEFAULT_LANG

# --- таблица переводов -----------------------------------------------------

_TR: dict[str, dict[str, str]] = {
    # --- общие элементы окна ---
    "app_title": {"uk": "Mission Analyzer", "en": "Mission Analyzer"},
    "app_subtitle": {
        "uk": "Аналіз місій ArduPilot / Mission Planner",
        "en": "ArduPilot / Mission Planner mission analysis",
    },
    "label_mission_file": {"uk": "Файл місії:", "en": "Mission file:"},
    "btn_browse": {"uk": "Огляд...", "en": "Browse..."},
    "label_params": {"uk": "Параметри аналізу", "en": "Analysis parameters"},

    # --- навигационные кнопки (иконка + подпись) ---
    "nav_mission": {"uk": "Місія", "en": "Mission"},
    "nav_analysis": {"uk": "Аналіз", "en": "Analysis"},
    "nav_config": {"uk": "Конфігурація", "en": "Configuration"},
    "nav_help": {"uk": "Довідка", "en": "Help"},
    "tab_help": {"uk": "Довідка", "en": "Help"},
    "tab_changelog": {"uk": "Історія змін", "en": "Changelog"},
    "label_version": {"uk": "версія", "en": "version"},

    "help_text_body": {
        "uk": (
            "MISSION ANALYZER — аналіз місій ArduPilot / Mission Planner (.waypoints)\n\n"
            "СТОРІНКИ\n"
            "Місія — «Завантажити» відкриває файл місії, одразу рахує повний аналіз і будує "
            "карту; таблиця точок (як у Mission Planner) і карта — тут же. «Зберегти» — звіт у CSV.\n\n"
            "Аналіз — результати: текстовий звіт, графік висоти (з рельєфом SRTM), графік "
            "кута нахилу траєкторії, глісада заходу на посадку.\n\n"
            "Конфігурація — пороги критичної висоти й кута повороту, папка SRTM (рельєф), "
            "папка диск-кешу тайлів карти, тип підложки карти (OpenStreetMap/Google), шар "
            "окупованих територій.\n\n"
            "Довідка — цей текст.\n\n"
            "ЩО ПЕРЕВІРЯЄТЬСЯ\n"
            "- критично низька висота (за файлом і за рельєфом SRTM, в точках і вздовж усієї "
            "лінії польоту між точками)\n"
            "- критично гострі кути повороту\n"
            "- кут нахилу траєкторії (набір/зниження) поза допуском\n\n"
            "НАЛАШТУВАННЯ\n"
            "Обрані папки, пороги, зум, мова тощо запам'ятовуються автоматично між запусками "
            "(файл settings.json поруч із програмою). Щоб скинути все на типові значення — "
            "просто видали цей файл.\n\n"
            "Карта якщо є інтернет, тайли качаються з OpenStreetMap/Google. Диск-кеш "
            "(необов'язковий) зберігає вже завантажені тайли, щоб не качати повторно.\n\n"
            "Мову інтерфейсу (UA/EN) можна переключити кнопками у верхньому правому куті."
        ),
        "en": (
            "MISSION ANALYZER — ArduPilot / Mission Planner (.waypoints) mission analysis\n\n"
            "PAGES\n"
            "Mission — \"Load\" opens a mission file, immediately runs the full analysis and "
            "builds the map; the waypoint table (like in Mission Planner) and the map are right "
            "here. \"Save\" exports the report as CSV.\n\n"
            "Analysis — results: text report, elevation graph (with SRTM terrain), flight path "
            "angle graph, landing approach glide slope.\n\n"
            "Configuration — critical altitude/turn-angle thresholds, SRTM terrain tile folder, "
            "map tile disk cache folder, map basemap type (OpenStreetMap/Google), occupied-"
            "territories layer.\n\n"
            "Help — this text.\n\n"
            "WHAT IS CHECKED\n"
            "- critically low altitude (from the file and from SRTM terrain, at points and along "
            "the whole flight line between points)\n"
            "- critically sharp turn angles\n"
            "- flight path angle (climb/descent) out of tolerance\n\n"
            "SETTINGS\n"
            "Chosen folders, thresholds, zoom, language etc. are remembered automatically between "
            "runs (settings.json next to the program). Delete that file to reset everything to "
            "defaults.\n\n"
            "Map: if there's internet, tiles are downloaded from OpenStreetMap/Google. The disk "
            "cache (optional) keeps already-downloaded tiles so they aren't re-downloaded.\n\n"
            "Interface language (UA/EN) can be switched with the buttons in the top-right corner."
        ),
    },
    "label_alt_min": {"uk": "Мін. висота, м:", "en": "Min altitude, m:"},
    "label_turn_min": {"uk": "Мін. кут повороту, °:", "en": "Min turn angle, °:"},
    "check_srtm": {"uk": "Рельєф (SRTM)", "en": "Terrain (SRTM)"},
    "label_map_cache": {"uk": "Диск-кеш карти (необов'язково):", "en": "Map disk cache (optional):"},
    "btn_analyze": {"uk": "Аналізувати", "en": "Analyze"},
    "btn_save_csv": {"uk": "Зберегти CSV...", "en": "Save CSV..."},
    "btn_load": {"uk": "Завантажити", "en": "Load"},
    "btn_save": {"uk": "Зберегти", "en": "Save"},
    "label_map_settings": {"uk": "Карта", "en": "Map"},

    "table_col_idx": {"uk": "#", "en": "#"},
    "table_col_command": {"uk": "Команда", "en": "Command"},
    "table_col_p1": {"uk": "P1", "en": "P1"},
    "table_col_p2": {"uk": "P2", "en": "P2"},
    "table_col_p3": {"uk": "P3", "en": "P3"},
    "table_col_p4": {"uk": "P4", "en": "P4"},
    "table_col_lat": {"uk": "Шир.", "en": "Lat"},
    "table_col_lon": {"uk": "Довг.", "en": "Lon"},
    "table_col_alt": {"uk": "Вис.", "en": "Alt"},
    "table_col_frame": {"uk": "Фрейм", "en": "Frame"},
    "table_col_dist": {"uk": "Відст, м", "en": "Dist, m"},
    "table_col_az": {"uk": "AZ, °", "en": "AZ, °"},
    "status_loaded_fmt": {
        "uk": "Завантажено: {n} точок маршруту",
        "en": "Loaded: {n} route points",
    },

    "tab_report": {"uk": "Звіт", "en": "Report"},
    "tab_elevation": {"uk": "Графік висоти", "en": "Elevation graph"},
    "tab_angle": {"uk": "Кут траєкторії", "en": "Path angle"},
    "tab_map": {"uk": "Карта", "en": "Map"},
    "tab_landing": {"uk": "Глісада", "en": "Glide slope"},

    "title_landing_approach": {"uk": "Глісада заходу на посадку", "en": "Landing approach glide slope"},
    "landing_no_data": {
        "uk": "Недостатньо даних для профілю заходу на посадку",
        "en": "Not enough data for the landing approach profile",
    },
    "landing_leg_label": {
        "uk": "{dist:.0f} м, азимут {bearing:.0f}°, кут {angle}",
        "en": "{dist:.0f} m, bearing {bearing:.0f}°, angle {angle}",
    },
    "landing_speed_marker_label": {
        "uk": "{command}: V={speed:.1f} м/с ({speed_type})",
        "en": "{command}: V={speed:.1f} m/s ({speed_type})",
    },

    "label_map_provider": {"uk": "Карта:", "en": "Map:"},
    "label_zoom": {"uk": "Зум:", "en": "Zoom:"},
    "hint_wheel_zoom": {
        "uk": "(або крути колесо миші над картою)",
        "en": "(or scroll mouse wheel over the map)",
    },
    "btn_update_map": {"uk": "Оновити карту", "en": "Update map"},
    "btn_cancel": {"uk": "Скасувати", "en": "Cancel"},
    "check_occupied": {
        "uk": "Окуповані території / лінія зіткнення (deepstatemap.live)",
        "en": "Occupied territories / line of contact (deepstatemap.live)",
    },

    "status_default": {
        "uk": "Обери файл місії і натисни «Аналізувати»",
        "en": "Choose a mission file and click \"Analyze\"",
    },

    # --- провайдеры карт ---
    "provider_osm": {"uk": "OpenStreetMap", "en": "OpenStreetMap"},
    "provider_google_roadmap": {"uk": "Google Карти (схема)", "en": "Google Maps (roadmap)"},
    "provider_google_satellite": {"uk": "Google Супутник", "en": "Google Satellite"},
    "provider_google_hybrid": {"uk": "Google Гібрид (супутник+підписи)", "en": "Google Hybrid (satellite+labels)"},

    # --- диалоги выбора файла/папки ---
    "dlg_choose_mission_title": {"uk": "Обери файл місії", "en": "Choose mission file"},
    "filetype_waypoints": {"uk": "Waypoints", "en": "Waypoints"},
    "filetype_all": {"uk": "Всі файли", "en": "All files"},
    "dlg_choose_srtm_title": {"uk": "Папка з SRTM-тайлами (.hgt)", "en": "Folder with SRTM tiles (.hgt)"},
    "dlg_choose_mapcache_title": {
        "uk": "Папка для локального диск-кешу тайлів карти",
        "en": "Folder for local map tile disk cache",
    },
    "dlg_save_csv_title": {"uk": "Зберегти звіт як...", "en": "Save report as..."},

    # --- сообщения ---
    "msg_no_file_title": {"uk": "Немає файлу", "en": "No file"},
    "msg_no_file_body": {"uk": "Спочатку обери файл місії", "en": "Choose a mission file first"},
    "msg_file_not_found_title": {"uk": "Помилка", "en": "Error"},
    "msg_file_not_found_body": {"uk": "Файл не знайдено:\n{path}", "en": "File not found:\n{path}"},
    "msg_bad_numbers_title": {"uk": "Помилка", "en": "Error"},
    "msg_bad_numbers_body": {
        "uk": "Пороги висоти/кута мають бути числами",
        "en": "Altitude/angle thresholds must be numbers",
    },
    "msg_file_read_error_title": {"uk": "Помилка читання файлу", "en": "File read error"},
    "msg_srtm_unavailable_title": {"uk": "SRTM недоступний", "en": "SRTM unavailable"},
    "msg_srtm_unavailable_body": {
        "uk": "{err}\n\nПродовжую аналіз без урахування рельєфу.",
        "en": "{err}\n\nContinuing analysis without terrain.",
    },
    "status_ready_fmt": {
        "uk": "Готово: {n} точок маршруту, {m} критичних відміток",
        "en": "Done: {n} route points, {m} critical flags",
    },
    "msg_no_data_title": {"uk": "Немає даних", "en": "No data"},
    "msg_no_data_body": {"uk": "Спочатку виконай аналіз", "en": "Run analysis first"},
    "msg_saved_title": {"uk": "Готово", "en": "Done"},
    "msg_saved_body": {"uk": "Звіт збережено:\n{path}", "en": "Report saved:\n{path}"},
    "msg_too_large_zoom_title": {"uk": "Занадто великий масштаб", "en": "Zoom level too large"},
    "msg_too_large_zoom_body": {
        "uk": "На цьому зумі потрібно {total} тайлів — це багато. Збільш зум (менша цифра) і повтори.",
        "en": "This zoom needs {total} tiles — that's a lot. Increase zoom (lower number) and retry.",
    },
    "msg_no_points_title": {"uk": "Немає точок", "en": "No points"},
    "msg_no_points_body": {
        "uk": "У місії немає точок з координатами",
        "en": "The mission has no points with coordinates",
    },
    "status_loading_tiles_fmt": {
        "uk": "Завантажую тайли: {done} з {total}...",
        "en": "Loading tiles: {done} of {total}...",
    },
    "status_cancelling": {"uk": "Скасовую...", "en": "Cancelling..."},
    "status_map_cancelled": {"uk": "Завантаження карти скасовано", "en": "Map loading cancelled"},
    "occupied_status_date_fmt": {"uk": "Дані окупації на {date}", "en": "Occupation data as of {date}"},
    "occupied_status_failed": {
        "uk": "Не вдалося завантажити шар окупації (немає мережі/кешу)",
        "en": "Failed to load occupation layer (no network/cache)",
    },
    "status_rendered_fmt": {
        "uk": "Відображено: {found} з {total} (не знайдено/немає мережі: {missing})",
        "en": "Rendered: {found} of {total} (not found/no network: {missing})",
    },
    "status_undecodable_suffix_fmt": {
        "uk": ", не декодовано (JPEG без Pillow): {n}",
        "en": ", undecodable (JPEG without Pillow): {n}",
    },
    "msg_need_pillow_title": {"uk": "Потрібен Pillow для JPEG-тайлів", "en": "Pillow needed for JPEG tiles"},
    "msg_need_pillow_body": {
        "uk": (
            "{n} тайлів — це JPEG, а Tkinter без Pillow їх не відкриває.\n\n"
            "Постав: python -m pip install Pillow\n\n"
            "Після встановлення просто натисни «Оновити карту» ще раз."
        ),
        "en": (
            "{n} tiles are JPEG, and Tkinter can't open them without Pillow.\n\n"
            "Install: python -m pip install Pillow\n\n"
            "After installing, just click \"Update map\" again."
        ),
    },

    # --- надписи на канвасах ---
    "title_elevation_profile": {"uk": "Профіль висоти місії", "en": "Mission elevation profile"},
    "title_angle_profile": {
        "uk": "Кут нахилу траєкторії (набір/зниження)",
        "en": "Flight path angle (climb/descent)",
    },
    "map_no_tile": {"uk": "немає тайла", "en": "no tile"},
    "map_jpeg_no_pillow": {"uk": "JPEG без Pillow", "en": "JPEG w/o Pillow"},

    # --- отчёт (analyzer.py) ---
    "report_nav_points": {"uk": "Точок маршруту: {n}", "en": "Route points: {n}"},
    "report_total_distance": {
        "uk": "Загальна дальність маршруту: {km:.2f} км",
        "en": "Total route distance: {km:.2f} km",
    },
    "report_note_land": {
        "uk": "точки посадки (#{idxs}) не перевіряються на критичну висоту — 0 м на посадці це норма",
        "en": "landing points (#{idxs}) are not checked for critical altitude — 0 m at touchdown is normal",
    },
    "report_note_no_pos": {
        "uk": (
            "точки без координат (#{idxs}, зазвичай TAKEOFF) перевіряються на висоту, "
            "але не беруть участі в дистанції/поворотах/AGL"
        ),
        "en": (
            "points without coordinates (#{idxs}, usually TAKEOFF) are checked for altitude "
            "but not used for distance/turns/AGL"
        ),
    },
    "report_no_critical": {"uk": "Критичних точок не знайдено.", "en": "No critical points found."},
    "report_landing_approach_title": {
        "uk": "Глісада заходу на посадку (дистанція / азимут / кут зниження):",
        "en": "Landing approach glide slope (distance / bearing / descent angle):",
    },
    "report_landing_leg_line": {
        "uk": "  WP {from_idx} -> WP {to_idx}: {dist:.0f} м, азимут {bearing:.0f}°, кут {angle}",
        "en": "  WP {from_idx} -> WP {to_idx}: {dist:.0f} m, bearing {bearing:.0f}°, angle {angle}",
    },
    "report_landing_speed_line": {
        "uk": "  Після WP {wp_index} спрацьовує DO_CHANGE_SPEED: швидкість обмежується {speed:.1f} м/с ({speed_type})",
        "en": "  After WP {wp_index}, DO_CHANGE_SPEED triggers: speed limited to {speed:.1f} m/s ({speed_type})",
    },
    "speed_type_0": {"uk": "повітряна", "en": "airspeed"},
    "speed_type_1": {"uk": "путьова", "en": "ground speed"},
    "speed_type_2": {"uk": "набору висоти", "en": "climb speed"},
    "speed_type_3": {"uk": "зниження", "en": "descent speed"},
    "report_count_suffix": {"uk": "{count} шт.", "en": "{count} pcs."},
    "report_wp_line": {"uk": "  WP #{idx}: {detail}", "en": "  WP #{idx}: {detail}"},

    "title_low_altitude": {
        "uk": "Критично низька висота за даними файлу, без урахування рельєфу (< {threshold:.0f} м)",
        "en": "Critically low altitude from raw file, terrain not considered (< {threshold:.0f} m)",
    },
    "title_low_agl": {
        "uk": "Критично низька висота над рельєфом, за SRTM (< {threshold:.0f} м)",
        "en": "Critically low altitude above terrain, from SRTM (< {threshold:.0f} m)",
    },
    "title_low_agl_segment": {
        "uk": "Критично низька висота НАД РЕЛЬЄФОМ між точками (лінія польоту перетинає рельєф) (< {threshold:.0f} м)",
        "en": "Critically low altitude above terrain BETWEEN points (flight line crosses terrain) (< {threshold:.0f} m)",
    },
    "title_sharp_turn": {
        "uk": "Критично гострий кут повороту (< {threshold:.0f}°)",
        "en": "Critically sharp turn angle (< {threshold:.0f}°)",
    },
    "title_steep_angle": {
        "uk": "Кут нахилу траєкторії поза допуском (|кут| > {threshold:.0f}°)",
        "en": "Flight path angle out of tolerance (|angle| > {threshold:.0f}°)",
    },
    "title_srtm_missing": {
        "uk": "Немає даних SRTM для точки (тайл не знайдено)",
        "en": "No SRTM data for point (tile not found)",
    },

    "detail_low_altitude": {
        "uk": "Висота {value:.1f} м < порогу {threshold:.1f} м",
        "en": "Altitude {value:.1f} m < threshold {threshold:.1f} m",
    },
    "detail_low_agl": {
        "uk": "Висота над рельєфом {value:.1f} м < порогу {threshold:.1f} м",
        "en": "Altitude above terrain {value:.1f} m < threshold {threshold:.1f} m",
    },
    "detail_low_agl_segment": {
        "uk": "Мінімальний запас висоти {value:.1f} м < порогу {threshold:.1f} м десь на ділянці WP {from_idx} -> WP {to_idx}",
        "en": "Minimum clearance {value:.1f} m < threshold {threshold:.1f} m somewhere on segment WP {from_idx} -> WP {to_idx}",
    },
    "detail_sharp_turn": {
        "uk": "Кут повороту {value:.2f}° < порогу {threshold:.1f}°",
        "en": "Turn angle {value:.2f}° < threshold {threshold:.1f}°",
    },
    "detail_steep_angle": {
        "uk": "Кут траєкторії {value:+.2f}° перевищує допустимі ±{threshold:.1f}° (ділянка WP {from_idx} -> WP {to_idx})",
        "en": "Path angle {value:+.2f}° exceeds allowed ±{threshold:.1f}° (segment WP {from_idx} -> WP {to_idx})",
    },
    "detail_srtm_missing": {
        "uk": "Немає даних висоти для точки ({lat:.5f}, {lon:.5f})",
        "en": "No elevation data for point ({lat:.5f}, {lon:.5f})",
    },
}


def set_lang(lang: str):
    global _current_lang
    if lang in LANGS:
        _current_lang = lang


def get_lang() -> str:
    return _current_lang


def t(key: str, **kwargs) -> str:
    entry = _TR.get(key)
    if entry is None:
        return key
    text = entry.get(_current_lang) or entry.get("en") or key
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, ValueError):
            pass
    return text


def format_issue_detail(issue: dict) -> str:
    """Строит локализованный текст 'детали' проблемы по её структурированным полям."""
    kind = issue.get("type")
    extra = issue.get("extra") or {}

    if kind == "LOW_ALTITUDE":
        return t("detail_low_altitude", value=issue["value"], threshold=issue["threshold"])
    if kind == "LOW_AGL":
        return t("detail_low_agl", value=issue["value"], threshold=issue["threshold"])
    if kind == "LOW_AGL_SEGMENT":
        return t(
            "detail_low_agl_segment",
            value=issue["value"], threshold=issue["threshold"],
            from_idx=extra.get("from_idx"), to_idx=extra.get("to_idx"),
        )
    if kind == "SHARP_TURN":
        return t("detail_sharp_turn", value=issue["value"], threshold=issue["threshold"])
    if kind == "STEEP_ANGLE":
        return t(
            "detail_steep_angle",
            value=issue["value"], threshold=issue["threshold"],
            from_idx=extra.get("from_idx"), to_idx=issue["wp_index"],
        )
    if kind == "SRTM_MISSING":
        return t("detail_srtm_missing", lat=extra.get("lat", 0.0), lon=extra.get("lon", 0.0))
    return ""


def speed_type_label(speed_type: int) -> str:
    return t(f"speed_type_{speed_type}") if f"speed_type_{speed_type}" in _TR else str(speed_type)


def issue_title(kind: str, threshold: float | None) -> str:
    key = {
        "LOW_ALTITUDE": "title_low_altitude",
        "LOW_AGL": "title_low_agl",
        "LOW_AGL_SEGMENT": "title_low_agl_segment",
        "SHARP_TURN": "title_sharp_turn",
        "STEEP_ANGLE": "title_steep_angle",
        "SRTM_MISSING": "title_srtm_missing",
    }.get(kind)
    if key is None:
        return kind
    if threshold is None:
        return t(key, threshold=0.0)
    return t(key, threshold=threshold)
