"""
overview_map.py — READ-ONLY карти для сторінки «Аналіз»: квадратні 4×4 км
навколо точок старту/посадки (Зліт/Глісада) і карта всього маршруту
«вигляд згори» (Траєкторія). Завжди на всю ширину вкладки, без зуму,
без панелі керування і БЕЗ можливості редагування місії.

Свідомо відокремлений від map_view.py: там — інтерактивна карта сторінки
«Місія» (з контролем зуму, у планах — редактор місії з перетягуванням
точок). Зміни в тій карті не повинні випадково зачепити ці, статичні,
огляди. Спільна лише "чиста" математика тайлів без стану --
compute_tile_bounds/fetch_tiles/_decode_tile_image беруться з map_view.py,
самі функції відмальовки тут повністю свої.
"""

from __future__ import annotations

import io
import math
import tkinter as tk

from analyzer import MissionAnalyzer
from geo import TILE_SIZE, lonlat_to_tile_xy, lonlat_to_pixel
from map_view import _decode_tile_image, MapTooLargeError

try:
    from PIL import Image, ImageTk
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False


def compute_area_tile_bounds(lat: float, lon: float, zoom: int,
                              half_km: float = 2.0, max_tiles: int = 400):
    """
    Повертає діапазон тайлів для квадрата half_km*2 × half_km*2 км
    з центром у точці (lat, lon). За замовчуванням — 4×4 км (half_km=2).
    """
    dlat = half_km / 111.0
    dlon = half_km / (111.0 * math.cos(math.radians(lat)))

    lat_min, lat_max = lat - dlat, lat + dlat
    lon_min, lon_max = lon - dlon, lon + dlon

    tx1, ty1 = lonlat_to_tile_xy(lat_max, lon_min, zoom)  # north-west
    tx2, ty2 = lonlat_to_tile_xy(lat_min, lon_max, zoom)  # south-east
    tx_min, tx_max = min(tx1, tx2), max(tx1, tx2)
    ty_min, ty_max = min(ty1, ty2), max(ty1, ty2)

    total = (tx_max - tx_min + 1) * (ty_max - ty_min + 1)
    if total > max_tiles:
        raise MapTooLargeError(total)

    return tx_min, tx_max, ty_min, ty_max, total


def _compose_scaled(tiles: dict, tx_min: int, tx_max: int, ty_min: int, ty_max: int,
                    target_w: int, target_h: int):
    """
    Збирає всі тайли в одне мозаїчне зображення і масштабує його РІВНО під
    (target_w, target_h) -- пікселі канваса. Без цього мозаїка тайлів
    (кратна 256px) майже ніколи точно не збігається з реальним розміром
    рамки, і карта виглядає ширшою/вужчою за неї. Повертає
    (PhotoImage, scale_x, scale_y) або None, якщо Pillow не встановлено
    (тоді викликач має намалювати тайли в натуральну величину як fallback).
    """
    if not _HAS_PIL:
        return None

    grid_w = (tx_max - tx_min + 1) * TILE_SIZE
    grid_h = (ty_max - ty_min + 1) * TILE_SIZE

    mosaic = Image.new("RGB", (grid_w, grid_h), "#cccccc")
    for tx in range(tx_min, tx_max + 1):
        for ty in range(ty_min, ty_max + 1):
            data = tiles.get((tx, ty))
            if not data:
                continue
            try:
                tile_img = Image.open(io.BytesIO(data)).convert("RGB")
            except Exception:
                continue
            px = (tx - tx_min) * TILE_SIZE
            py = (ty - ty_min) * TILE_SIZE
            mosaic.paste(tile_img, (px, py))

    target_w = max(int(target_w), 1)
    target_h = max(int(target_h), 1)
    resized = mosaic.resize((target_w, target_h), Image.LANCZOS)
    photo = ImageTk.PhotoImage(resized)
    return photo, target_w / grid_w, target_h / grid_h


def _draw_tiles(canvas: tk.Canvas, tiles: dict, image_refs: list,
                tx_min: int, tx_max: int, ty_min: int, ty_max: int,
                origin_x: float, origin_y: float):
    for tx in range(tx_min, tx_max + 1):
        for ty in range(ty_min, ty_max + 1):
            data = tiles.get((tx, ty))
            px = tx * TILE_SIZE - origin_x
            py = ty * TILE_SIZE - origin_y
            if data:
                img = _decode_tile_image(data)
                if img:
                    image_refs.append(img)
                    canvas.create_image(px, py, image=img, anchor="nw")
                    continue
            canvas.create_rectangle(px, py, px + TILE_SIZE, py + TILE_SIZE,
                                    fill="#cccccc", outline="#aaaaaa")


def render_area_map(canvas: tk.Canvas, lat: float, lon: float, zoom: int,
                    tiles: dict, image_refs: list,
                    tx_min: int, tx_max: int, ty_min: int, ty_max: int,
                    flight_az: float | None = None,
                    wind_dir: float | None = None,
                    wind_spd: float | None = None):
    """
    Рисує квадрат 4×4 км навколо точки (lat, lon): тайли (масштабовані
    рівно під розмір канваса) + стрілки азимуту польоту і вітру поверх
    карти. Read-only, без редагування.
    """
    canvas.delete("all")
    image_refs.clear()

    canvas.update_idletasks()
    W = max(canvas.winfo_width(), 100)
    H = max(canvas.winfo_height(), 100)
    R = min(W, H) // 2 - 16

    origin_x = tx_min * TILE_SIZE
    origin_y = ty_min * TILE_SIZE

    composed = _compose_scaled(tiles, tx_min, tx_max, ty_min, ty_max, W, H)
    if composed:
        photo, scale_x, scale_y = composed
        image_refs.append(photo)
        canvas.create_image(0, 0, image=photo, anchor="nw")
    else:
        # Pillow не встановлено -- малюємо тайли в натуральну величину
        # (карта може не збігатись пиксель-в-піксель з рамкою)
        _draw_tiles(canvas, tiles, image_refs, tx_min, tx_max, ty_min, ty_max, origin_x, origin_y)
        scale_x = scale_y = 1.0

    cx_px, cy_px = lonlat_to_pixel(lat, lon, zoom)
    cx = (cx_px - origin_x) * scale_x
    cy = (cy_px - origin_y) * scale_y

    canvas.create_oval(cx - 6, cy - 6, cx + 6, cy + 6,
                       fill="#FFFFFF", outline="#000000", width=2)

    for ang, lbl in ((0, "N"), (90, "E"), (180, "S"), (270, "W")):
        rad = math.radians(ang)
        x = cx + (R + 14) * math.sin(rad)
        y = cy - (R + 14) * math.cos(rad)
        canvas.create_text(x, y, text=lbl, fill="#000000",
                           font=("Segoe UI", 8, "bold"), tags="overlay")

    def arrow(az_deg: float, length: float, color: str, width: int,
              lbl: str, lbl_color: str):
        rad = math.radians(az_deg)
        ex = cx + length * math.sin(rad)
        ey = cy - length * math.cos(rad)
        canvas.create_line(cx, cy, ex, ey, fill=color, width=width,
                           arrow="last", arrowshape=(12, 14, 5), tags="overlay")
        lx = cx + (length + 20) * math.sin(rad)
        ly = cy - (length + 20) * math.cos(rad)
        canvas.create_text(lx, ly, text=lbl, fill=lbl_color,
                           font=("Segoe UI", 8, "bold"), tags="overlay")

    if flight_az is not None:
        arrow(flight_az, R * 0.70, "#39FF14", 3, f"Az {flight_az:.0f}°", "#39FF14")

    if wind_dir is not None:
        wind_to = (wind_dir + 180) % 360
        arrow(wind_to, R * 0.60, "#00BFFF", 3, f"{wind_spd:.0f}км/г\n{wind_dir:.0f}°", "#00BFFF")

        if flight_az is not None:
            diff = abs((wind_dir - flight_az + 360) % 360)
            if diff > 180:
                diff = 360 - diff
            cross = abs(90 - abs(diff - 90))
            color = "#FF4444" if cross > 30 else "#44FF88"
            canvas.create_rectangle(4, H - 22, W - 4, H - 4,
                                    fill="#000000", outline="", stipple="gray50", tags="overlay")
            canvas.create_text(W // 2, H - 12, fill=color, font=("Segoe UI", 8, "bold"),
                               text=f"Боковий вітер: {cross:.0f}°", tags="overlay")

    canvas.config(scrollregion=(0, 0, W, H))


def render_route_overview(canvas: tk.Canvas, analyzer: MissionAnalyzer, zoom: int,
                          tx_min: int, tx_max: int, ty_min: int, ty_max: int,
                          tiles: dict, image_refs: list):
    """
    Карта всього маршруту «вигляд згори» для вкладки «Траєкторія».
    Read-only: лінія маршруту + точки, БЕЗ панелі керування зумом і БЕЗ
    можливості перетягувати/редагувати точки (на відміну від майбутнього
    редактора місій на сторінці «Місія» -- це навмисно окрема функція).
    """
    canvas.delete("all")
    image_refs.clear()

    canvas.update_idletasks()
    W = max(canvas.winfo_width(), 100)
    H = max(canvas.winfo_height(), 100)

    origin_x = tx_min * TILE_SIZE
    origin_y = ty_min * TILE_SIZE

    composed = _compose_scaled(tiles, tx_min, tx_max, ty_min, ty_max, W, H)
    if composed:
        photo, scale_x, scale_y = composed
        image_refs.append(photo)
        canvas.create_image(0, 0, image=photo, anchor="nw")
    else:
        _draw_tiles(canvas, tiles, image_refs, tx_min, tx_max, ty_min, ty_max, origin_x, origin_y)
        scale_x = scale_y = 1.0

    issues_by_wp: dict[int, list[str]] = {}
    for it in analyzer.issues:
        issues_by_wp.setdefault(it["wp_index"], []).append(it["type"])

    route_px = []
    for wp in analyzer.nav_wps:
        gx, gy = lonlat_to_pixel(wp.lat, wp.lon, zoom)
        route_px.append(((gx - origin_x) * scale_x, (gy - origin_y) * scale_y, wp))

    for i in range(len(route_px) - 1):
        x1, y1, _ = route_px[i]
        x2, y2, _ = route_px[i + 1]
        canvas.create_line(x1, y1, x2, y2, fill="#1f77b4", width=3)

    for x, y, wp in route_px:
        color = "#d62728" if wp.index in issues_by_wp else "#1f77b4"
        canvas.create_oval(x - 6, y - 6, x + 6, y + 6, fill=color, outline="white", width=1)
        canvas.create_text(x, y - 12, text=str(wp.index), font=("Arial", 8, "bold"))

    canvas.config(scrollregion=(0, 0, W, H))
