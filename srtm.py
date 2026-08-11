"""
srtm.py — чтение высоты рельефа из тайлов SRTM (тот же формат, что
кэширует Mission Planner в папке .../Mission Planner/srtm/). Тайлы
ищутся по имени файла (N50E030 и т.п.), расширение не важно — на
некоторых компьютерах .hgt может быть переименован (например в .doc
из-за политики безопасности), содержимое при этом не меняется.
"""

from __future__ import annotations

import sys
import re
import math
import array
from pathlib import Path

_TILE_NAME_RE = re.compile(r"^[NS]\d{2}[EW]\d{3}$")


class SRTMError(Exception):
    pass


class SRTMTerrain:
    """
    Поддерживает SRTM1 (3601x3601, шаг 1") и SRTM3 (1201x1201, шаг 3"),
    определяет тип по размеру файла. Расширение файла может быть любым —
    тайл определяется по имени (например N50E030), не по расширению.
    Если тайла для точки нет, кидается SRTMError.
    """

    VOID = -32768  # признак отсутствующих данных в SRTM

    def __init__(self, srtm_dir: str):
        self.srtm_dir = Path(srtm_dir)
        if not self.srtm_dir.is_dir():
            raise SRTMError(f"Папка с SRTM-тайлами не найдена: {srtm_dir}")
        self._index: dict[str, Path] | None = None
        self._cache: dict[str, tuple[array.array, int]] = {}

    @staticmethod
    def _tile_name(lat: float, lon: float) -> str:
        lat_i = math.floor(lat)
        lon_i = math.floor(lon)
        ns = "N" if lat_i >= 0 else "S"
        ew = "E" if lon_i >= 0 else "W"
        return f"{ns}{abs(lat_i):02d}{ew}{abs(lon_i):03d}"

    def _build_index(self):
        self._index = {}
        for p in self.srtm_dir.rglob("*"):
            if not p.is_file():
                continue
            stem = p.stem.upper()
            if _TILE_NAME_RE.match(stem):
                self._index[stem] = p

    def _find_tile_path(self, name: str) -> Path:
        if self._index is None:
            self._build_index()
        path = self._index.get(name.upper())
        if path is None:
            raise SRTMError(
                f"Не найден тайл {name} (любое расширение) в {self.srtm_dir} "
                f"(нужен для точки маршрута)"
            )
        return path

    def _load_tile(self, name: str) -> tuple[array.array, int]:
        if name in self._cache:
            return self._cache[name]
        path = self._find_tile_path(name)
        size_bytes = path.stat().st_size
        n_samples = int(round((size_bytes / 2) ** 0.5))
        if n_samples not in (1201, 3601):
            raise SRTMError(f"Неожиданный размер тайла {path}: {size_bytes} байт")
        with open(path, "rb") as f:
            data = f.read()
        arr = array.array("h")
        arr.frombytes(data)
        if sys.byteorder == "little":
            arr.byteswap()  # .hgt хранится big-endian
        self._cache[name] = (arr, n_samples)
        return arr, n_samples

    def get_elevation(self, lat: float, lon: float) -> float:
        """Высота рельефа (м, AMSL) в точке, билинейная интерполяция по тайлу."""
        name = self._tile_name(lat, lon)
        arr, size = self._load_tile(name)

        tile_lat = math.floor(lat)
        tile_lon = math.floor(lon)
        # строка 0 = северная граница тайла (tile_lat+1), последняя = южная (tile_lat)
        row_f = (tile_lat + 1 - lat) * (size - 1)
        col_f = (lon - tile_lon) * (size - 1)

        row_f = min(max(row_f, 0), size - 1)
        col_f = min(max(col_f, 0), size - 1)

        r0, c0 = int(math.floor(row_f)), int(math.floor(col_f))
        r1, c1 = min(r0 + 1, size - 1), min(c0 + 1, size - 1)
        fr, fc = row_f - r0, col_f - c0

        def sample(r, c):
            return arr[r * size + c]

        v00, v01 = sample(r0, c0), sample(r0, c1)
        v10, v11 = sample(r1, c0), sample(r1, c1)

        if self.VOID in (v00, v01, v10, v11):
            # хотя бы один угол — "дырка" в данных; берём ближайший непустой
            for v in (v00, v01, v10, v11):
                if v != self.VOID:
                    return float(v)
            raise SRTMError(f"Нет данных высоты для точки ({lat}, {lon}) — VOID")

        top = v00 * (1 - fc) + v01 * fc
        bot = v10 * (1 - fc) + v11 * fc
        return top * (1 - fr) + bot * fr
