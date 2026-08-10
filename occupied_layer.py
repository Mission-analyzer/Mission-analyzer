"""
occupied_layer.py — слой оккупированных территорий / линии соприкосновения
поверх карты. Источник: общедоступный, ежедневно обновляемый GeoJSON-мираж
данных deepstatemap.live (репозиторий cyterat/deepstate-map-data на GitHub,
собирает данные из открытого OSINT-проекта Deep State UA).

Каждый день публикуется отдельный лёгкий файл (~50-100 КБ) с текущей формой
оккупированной территории (MultiPolygon). Если файл за сегодня ещё не
опубликован (обновление раз в сутки, ~03:00 UTC) — скрипт откатывается на
более ранние даты, пока не найдёт последний доступный.

Кэшируется на диск по дате — при повторном запуске в тот же день сеть не
трогается вообще.
"""

from __future__ import annotations

import datetime
import json
import urllib.request
from pathlib import Path
from urllib.error import HTTPError, URLError

BASE_URL = "https://raw.githubusercontent.com/cyterat/deepstate-map-data/main/data/deepstatemap_data_{date}.geojson"


def fetch_occupied_geojson(
    cache_dir: str,
    max_days_back: int = 6,
    timeout: float = 8.0,
) -> tuple[dict | None, str | None]:
    """
    Возвращает (geojson_dict, дата_в_формате_YYYYMMDD) для последнего
    доступного дня, или (None, None), если ничего не нашлось (нет сети
    и нет кэша). Сначала проверяет диск, потом лезет в сеть.
    """
    cache_path_dir = Path(cache_dir) / "deepstate"
    cache_path_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.date.today()
    for delta in range(max_days_back):
        d = today - datetime.timedelta(days=delta)
        date_str = d.strftime("%Y%m%d")
        cache_file = cache_path_dir / f"deepstatemap_data_{date_str}.geojson"

        if cache_file.exists():
            try:
                return json.loads(cache_file.read_text(encoding="utf-8")), date_str
            except (OSError, json.JSONDecodeError):
                pass  # повреждённый кэш — попробуем скачать заново

        url = BASE_URL.format(date=date_str)
        req = urllib.request.Request(
            url, headers={"User-Agent": "MissionAnalyzer/1.0 (personal UAV mission-planning tool)"}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            data = json.loads(raw)
            try:
                cache_file.write_bytes(raw)
            except OSError:
                pass
            return data, date_str
        except (URLError, HTTPError, TimeoutError, OSError, json.JSONDecodeError):
            continue  # пробуем на день раньше

    return None, None


def extract_polygons(geojson: dict) -> list:
    """
    Возвращает список полигонов из GeoJSON. Каждый полигон — список колец
    [[(lon,lat), ...], ...], где кольцо 0 — внешний контур, остальные —
    дырки (дырки при отрисовке игнорируются, это только для наглядности,
    не для точных измерений).
    """
    polygons = []
    for feat in geojson.get("features", []):
        geom = feat.get("geometry") or {}
        gtype = geom.get("type")
        coords = geom.get("coordinates", [])
        if gtype == "MultiPolygon":
            polygons.extend(coords)
        elif gtype == "Polygon":
            polygons.append(coords)
    return polygons
