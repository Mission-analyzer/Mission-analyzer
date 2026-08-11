"""
theme.py — визуальная тема "а-ля Mission Planner": тёмно-синяя шапка и
статус-бар, синие акцентные кнопки, вкладки с подсветкой активной.
Ничего не знает про конкретные виджеты приложения — просто настраивает
ttk.Style и возвращает палитру цветов для точечного использования в app.py.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# Палитра в духе Mission Planner: тёмно-синяя шапка/тулбар, светлый фон
# рабочей области, синий акцент на активных элементах.
NAVY_DARK = "#0F2438"   # тёмно-синий — навигационная панель, статус-бар, активные вкладки
NAVY = "#173553"        # чуть светлее — неактивные тёмные элементы
HEADER_BG = "#000000"   # чёрный — фон шапки (лого/заголовок/переключатель языка)
# зелёный акцент для кнопок -- взят из логотипа (маршрут/точки/"ANALYZER")
GREEN_ACCENT = "#5A961E"
GREEN_ACCENT_HOVER = "#6FB026"
GREEN_DARK = "#355E12"
LIGHT_BG = "#F1F3F5"    # фон рабочей области
PANEL_BG = "#FFFFFF"    # фон полей ввода/списков
TEXT_DARK = "#1B1B1B"
TEXT_LIGHT = "#FFFFFF"
TEXT_MUTED = "#9FB6CC"  # приглушённый текст на тёмном фоне (статус-бар)
BORDER = "#C9CFD6"

PALETTE = {
    "navy_dark": NAVY_DARK,
    "navy": NAVY,
    "header_bg": HEADER_BG,
    "blue": GREEN_ACCENT,        # оставлено под старым ключом ради обратной совместимости в app.py
    "blue_hover": GREEN_ACCENT_HOVER,
    "green": GREEN_ACCENT,
    "green_hover": GREEN_ACCENT_HOVER,
    "green_dark": GREEN_DARK,
    "bg": LIGHT_BG,
    "panel": PANEL_BG,
    "text": TEXT_DARK,
    "text_light": TEXT_LIGHT,
    "text_muted": TEXT_MUTED,
    "border": BORDER,
}

_FONT_FAMILY = "Segoe UI"  # на Windows есть всегда; на других ОС ttk сам подберёт похожий


def apply_theme(root: tk.Tk) -> dict:
    """Настраивает ttk.Style под тему Mission Planner. Возвращает палитру цветов."""
    style = ttk.Style(root)
    # 'clam' — единственная встроенная тема, которая реально позволяет
    # перекрашивать фон/акценты кросс-платформенно (native-темы Windows
    # игнорируют часть настроек цвета)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    root.configure(bg=LIGHT_BG)

    base_font = (_FONT_FAMILY, 9)
    bold_font = (_FONT_FAMILY, 9, "bold")

    style.configure(".", background=LIGHT_BG, foreground=TEXT_DARK, font=base_font)
    style.configure("TFrame", background=LIGHT_BG)
    style.configure("TLabel", background=LIGHT_BG, foreground=TEXT_DARK)
    style.configure("TCheckbutton", background=LIGHT_BG, foreground=TEXT_DARK)
    style.map("TCheckbutton", background=[("active", LIGHT_BG)])

    style.configure("TLabelframe", background=LIGHT_BG, bordercolor=BORDER)
    style.configure("TLabelframe.Label", background=LIGHT_BG, foreground=NAVY, font=bold_font)

    style.configure("TEntry", fieldbackground=PANEL_BG, foreground=TEXT_DARK, bordercolor=BORDER)
    style.configure("TSpinbox", fieldbackground=PANEL_BG, foreground=TEXT_DARK, bordercolor=BORDER, arrowsize=12)
    style.configure("TCombobox", fieldbackground=PANEL_BG, foreground=TEXT_DARK, bordercolor=BORDER)
    style.map("TCombobox", fieldbackground=[("readonly", PANEL_BG)])

    # обычные кнопки — зелёный акцент (як в лого), як основні дії в MP
    style.configure(
        "TButton", background=GREEN_ACCENT, foreground=TEXT_LIGHT,
        borderwidth=0, focusthickness=0, padding=(12, 6), font=bold_font,
    )
    style.map(
        "TButton",
        background=[("disabled", "#B7C4D1"), ("pressed", GREEN_DARK), ("active", GREEN_ACCENT_HOVER)],
        foreground=[("disabled", "#E8ECEF")],
    )

    # нейтральные кнопки — тот же спокойный вид, что у вкладок на страницах
    # "Аналіз"/"Довідка" (без синей заливки), для действий, которые не
    # нужно акцентировать как основные (напр. "Завантажити"/"Зберегти").
    # При нажатии — та же логика, что у остальных элементов навигации:
    # тёмный/чёрный фон + "утопленный" вид кнопки (чуть меньше на вид).
    style.configure(
        "Secondary.TButton", background="#DEE3E8", foreground=TEXT_DARK,
        borderwidth=0, focusthickness=0, padding=(12, 6), font=bold_font,
    )
    style.map(
        "Secondary.TButton",
        background=[("disabled", "#EDEFF1"), ("pressed", HEADER_BG), ("active", "#C9CFD6")],
        foreground=[("disabled", "#9AA5AE"), ("pressed", TEXT_LIGHT)],
        padding=[("pressed", (10, 5))],
    )

    # вкладки блокнота — светло-серые неактивные, тёмно-синие активные
    style.configure("TNotebook", background=LIGHT_BG, borderwidth=0, tabmargins=(2, 4, 2, 0))
    style.configure(
        "TNotebook.Tab", background="#DEE3E8", foreground=TEXT_DARK,
        padding=(14, 7), font=bold_font, borderwidth=0,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", NAVY_DARK)],
        foreground=[("selected", TEXT_LIGHT)],
        expand=[("selected", (1, 1, 1, 0))],
    )

    # полосы прокрутки — нейтральные, но не выбивающиеся из темы
    style.configure("TScrollbar", background="#DEE3E8", troughcolor=LIGHT_BG, bordercolor=BORDER, arrowsize=12)

    # шапка окна
    style.configure("Header.TFrame", background=HEADER_BG)
    style.configure("Header.TLabel", background=HEADER_BG, foreground=TEXT_LIGHT, font=(_FONT_FAMILY, 13, "bold"))
    style.configure("HeaderSub.TLabel", background=HEADER_BG, foreground=TEXT_MUTED, font=(_FONT_FAMILY, 9))

    # статус-бар
    style.configure("Status.TFrame", background=NAVY_DARK)
    style.configure("Status.TLabel", background=NAVY_DARK, foreground=TEXT_MUTED, font=base_font)

    # переключатель языка — маленькие кнопки-тумблеры на тёмном фоне шапки
    style.configure(
        "LangToggle.TButton", background=GREEN_DARK, foreground=TEXT_MUTED,
        borderwidth=0, padding=(8, 4), font=bold_font,
    )
    style.map(
        "LangToggle.TButton",
        background=[("pressed", GREEN_ACCENT), ("active", HEADER_BG)],
        foreground=[("pressed", TEXT_LIGHT)],
    )
    style.configure(
        "LangToggleActive.TButton", background=GREEN_ACCENT, foreground=TEXT_LIGHT,
        borderwidth=0, padding=(8, 4), font=bold_font,
    )
    style.map("LangToggleActive.TButton", background=[("active", GREEN_ACCENT_HOVER)])

    return PALETTE


def set_window_icon(root: tk.Tk, path: str) -> bool:
    """
    Пробует установить иконку окна из файла (.png/.gif через PhotoImage,
    .ico через iconbitmap на Windows). Возвращает True при успехе, иначе
    молча отступает — отсутствие иконки не должно ронять программу.
    """
    try:
        if path.lower().endswith(".ico"):
            root.iconbitmap(path)
        else:
            img = tk.PhotoImage(file=path)
            root.iconphoto(True, img)
            root._icon_ref = img  # держим ссылку, иначе GC съест картинку
        return True
    except (tk.TclError, FileNotFoundError):
        return False
