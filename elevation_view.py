"""
elevation_view.py — отрисовка профиля высоты миссии на tk.Canvas.
Ничего не знает про Waypoint/парсинг — только берёт готовые данные
из MissionAnalyzer.elevation_profile() и рисует.
"""

from __future__ import annotations

import tkinter as tk

from analyzer import MissionAnalyzer
import i18n


def draw_elevation_profile(
    canvas: tk.Canvas, analyzer: MissionAnalyzer, step_m: float = 50.0,
    max_dist_m: float | None = None, title: str | None = None,
):
    """
    Полностью перерисовывает canvas профилем высоты текущей миссии.
    Якщо задано max_dist_m -- обрізає профіль по відстані (для зльоту,
    де потрібні лише перші кілька точок, а не весь маршрут).
    """
    canvas.delete("all")
    if analyzer is None:
        return

    width = max(canvas.winfo_width(), 200)
    height = max(canvas.winfo_height(), 150)

    try:
        profile = analyzer.elevation_profile(step_m=step_m)
    except ValueError:
        return

    dist_m_all = profile["dist"]
    mission_all = profile["mission_alt"]
    terrain_all = profile["terrain_alt"]
    waypoints_all = profile["waypoints"]

    if max_dist_m is not None:
        cut = next((i for i, d in enumerate(dist_m_all) if d > max_dist_m), len(dist_m_all))
        cut = max(cut, 2)  # хоча б дві точки, інакше нема що малювати
        dist_m = dist_m_all[:cut]
        mission_vals = mission_all[:cut]
        terrain_vals = terrain_all[:cut]
        waypoints = [wp for wp in waypoints_all if wp[0] <= dist_m[-1]]
    else:
        dist_m = dist_m_all
        mission_vals = mission_all
        terrain_vals = terrain_all
        waypoints = waypoints_all

    dist_km = [d / 1000 for d in dist_m]
    has_terrain = analyzer.terrain is not None and any(v is not None for v in terrain_vals)

    margin_l, margin_r, margin_t, margin_b = 55, 15, 25, 35
    plot_w = max(width - margin_l - margin_r, 10)
    plot_h = max(height - margin_t - margin_b, 10)

    all_alts = [v for v in mission_vals if v is not None]
    if has_terrain:
        all_alts += [v for v in terrain_vals if v is not None]
    if not all_alts:
        return

    x_min, x_max = 0.0, dist_km[-1] if dist_km[-1] > 0 else 1.0
    y_min, y_max = min(all_alts), max(all_alts)
    if y_max == y_min:
        y_max += 1.0
    y_pad = (y_max - y_min) * 0.08
    y_min -= y_pad
    y_max += y_pad

    def X(d_km):
        return margin_l + (d_km - x_min) / (x_max - x_min) * plot_w

    def Y(alt):
        return margin_t + (1 - (alt - y_min) / (y_max - y_min)) * plot_h

    # сетка и подписи
    for i in range(6):
        val = y_min + (y_max - y_min) * i / 5
        y = Y(val)
        canvas.create_line(margin_l, y, width - margin_r, y, fill="#e0e0e0")
        canvas.create_text(margin_l - 6, y, text=f"{val:.0f}", anchor="e", font=("Arial", 8))

    for i in range(7):
        val = x_min + (x_max - x_min) * i / 6
        x = X(val)
        canvas.create_line(x, margin_t, x, height - margin_b, fill="#f0f0f0")
        canvas.create_text(x, height - margin_b + 14, text=f"{val:.0f}", anchor="n", font=("Arial", 8))

    # рельеф (заливка) + подсветка зон низкого AGL
    if has_terrain:
        floor_y = Y(y_min)
        poly = [(X(d), Y(t)) for d, t in zip(dist_km, terrain_vals) if t is not None]
        if poly:
            pts = [(X(dist_km[0]), floor_y)] + poly + [(X(dist_km[-1]), floor_y)]
            flat = [c for p in pts for c in p]
            canvas.create_polygon(*flat, fill="#c9a27a", outline="#5C3A1E")

        low = [
            (m is not None and t is not None and (m - t) < analyzer.alt_min)
            for m, t in zip(mission_vals, terrain_vals)
        ]
        i = 0
        while i < len(low):
            if low[i]:
                j = i
                while j < len(low) and low[j]:
                    j += 1
                x1, x2 = X(dist_km[i]), X(dist_km[min(j, len(low) - 1)])
                canvas.create_rectangle(
                    x1, margin_t, x2, height - margin_b,
                    fill="#ff9999", outline="", stipple="gray50",
                )
                i = j
            else:
                i += 1

    # линия высоты миссии
    pts = [(X(d), Y(m)) for d, m in zip(dist_km, mission_vals) if m is not None]
    for i in range(len(pts) - 1):
        canvas.create_line(*pts[i], *pts[i + 1], fill="#1f77b4", width=2)

    # точки waypoint'ов
    for d, a, idx, seq in waypoints:
        if a is None:
            continue
        x, y = X(d / 1000), Y(a)
        canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill="red", outline="")
        canvas.create_text(x, y - 10, text=str(seq), font=("Arial", 8))

    canvas.create_text(width / 2, 12, text=title or i18n.t("title_elevation_profile"), font=("Arial", 11, "bold"))


def draw_takeoff_profile(canvas: tk.Canvas, analyzer: MissionAnalyzer, n_wps: int = 3, step_m: float = 10.0):
    """
    Профіль висоти лише для зльоту: точка старту + перші n_wps точок
    маршруту (детальніше, ніж загальний профіль -- крок 10 м замість 50).
    """
    if analyzer is None:
        canvas.delete("all")
        return
    try:
        profile = analyzer.elevation_profile(step_m=step_m)
    except ValueError:
        canvas.delete("all")
        return

    waypoints = profile["waypoints"]
    if len(waypoints) < 2:
        max_dist = None  # замало точок -- покажемо все, що є
    else:
        cutoff_wp = waypoints[min(n_wps, len(waypoints)) - 1]
        max_dist = cutoff_wp[0] * 1.08  # трохи запасу праворуч від останньої точки

    draw_elevation_profile(
        canvas, analyzer, step_m=step_m, max_dist_m=max_dist,
        title="Профіль висоти — зліт",
    )
