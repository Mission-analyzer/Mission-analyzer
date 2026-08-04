"""
settings.py — сохранение/загрузка настроек программы между запусками
(пути к папкам SRTM/кэша карт, пороги анализа, язык, зум, провайдер карты
и т.п.) в простой JSON-файл рядом с программой.
"""

from __future__ import annotations

import json
from pathlib import Path


def load_settings(path: str) -> dict:
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_settings(path: str, data: dict) -> None:
    try:
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
