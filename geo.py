"""
geo.py — геометрия маршрута (дистанции, углы) и картографические
преобразования (lat/lon <-> тайлы/пиксели слиппи-карты). Без зависимостей.
"""

from __future__ import annotations

import math

from waypoints import Waypoint

EARTH_RADIUS_M = 6371000.0
TILE_SIZE = 256


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Начальный азимут (истинный курс) от точки (lat1,lon1) к (lat2,lon2), 0-360°, 0=север."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlmb = math.radians(lon2 - lon1)
    x = math.sin(dlmb) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dlmb)
    brg = math.degrees(math.atan2(x, y))
    return (brg + 360.0) % 360.0


def vertex_angle_deg(a: Waypoint, b: Waypoint, c: Waypoint) -> float:
    """
    Угол в вершине B между отрезками B->A и B->C, в локальной плоской
    проекции (долгота масштабируется по cos(широты)).
    180° — прямая линия, 0° — разворот на месте.
    """
    lat0 = math.radians(b.lat)
    kx = math.cos(lat0)

    def to_xy(p: Waypoint) -> tuple[float, float]:
        return ((p.lon - b.lon) * kx, (p.lat - b.lat))

    ax, ay = to_xy(a)
    cx, cy = to_xy(c)

    n1 = math.hypot(ax, ay)
    n2 = math.hypot(cx, cy)
    if n1 == 0 or n2 == 0:
        return float("nan")

    cos_ang = (ax * cx + ay * cy) / (n1 * n2)
    cos_ang = max(-1.0, min(1.0, cos_ang))
    return math.degrees(math.acos(cos_ang))


# --- слиппи-карта (Web Mercator), используется для тайлов и офлайн-карты ---

def lonlat_to_tile_xy(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    lat = max(min(lat, 85.05112878), -85.05112878)
    lat_rad = math.radians(lat)
    n = 2 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def lonlat_to_pixel(lat: float, lon: float, zoom: int, tile_size: int = TILE_SIZE) -> tuple[float, float]:
    lat = max(min(lat, 85.05112878), -85.05112878)
    lat_rad = math.radians(lat)
    n = 2 ** zoom
    x = (lon + 180.0) / 360.0 * n * tile_size
    y = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n * tile_size
    return x, y
