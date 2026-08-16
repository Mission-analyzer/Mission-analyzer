"""
app.py — GUI-оболочка Mission Analyzer. Сама ничего не анализирует и не
парсит .waypoints — только собирает интерфейс (шапку, навбар, каркас
страниц) и делегирует построение и логику каждой страницы миксинам:
mission_page.MissionPageMixin, analysis_page.AnalysisPageMixin,
config_page.ConfigPageMixin, help_page.HelpPageMixin.

Локализация — через i18n.py (украинский/английский, без русского).
"""

from __future__ import annotations

import os
import sys
import threading
import tkinter as tk
from tkinter import ttk

from online_tiles import OnlineTileCache, PROVIDERS
from analyzer import MissionAnalyzer
import i18n
import theme
import icons
import settings

from mission_page import MissionPageMixin
from analysis_page import AnalysisPageMixin
from config_page import ConfigPageMixin
from help_page import HelpPageMixin

DEFAULT_ZOOM = 9
DEFAULT_PROVIDER_KEY = "google_hybrid"


def _app_base_dir() -> str:
    """Тека поруч з .exe (у зібраній версії) або з app.py (при запуску
    з вихідників). Сюди пишемо settings.json -- саме тут користувач
    очікує знайти конфіг, і саме звідси природно "видно" сусідні папки
    srtm/, map_cache/, які він сам створює поруч з програмою.

    НЕ те саме, що _bundled_asset_dir() нижче: PyInstaller-івський
    sys._MEIPASS (тека _internal у onedir-збірці) перестворюється
    заново при КОЖНІЙ пересборці .exe -- якщо туди ж писати
    settings.json, налаштування користувача губилися б щоразу, коли
    пересобираємо програму.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _bundled_asset_dir() -> str:
    """Тека з ресурсами, які PyInstaller вклав у збірку (icon.png тощо,
    див. datas у main.spec). У зібраній версії це sys._MEIPASS
    (у onedir-збірці фізично тека _internal поруч з .exe), при
    запуску з вихідників -- та сама тека, де лежить сам app.py.
    """
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.dirname(os.path.abspath(__file__))


class App(MissionPageMixin, AnalysisPageMixin, ConfigPageMixin, HelpPageMixin, tk.Tk):
    ICON_CANDIDATES = ("icon.png", "logo.png", "icon.ico", "logo.ico")

    def __init__(self):
        super().__init__()

        self._settings_data = settings.load_settings(self._settings_path())
        saved_lang = self._settings_data.get("lang")
        if saved_lang in i18n.LANGS:
            i18n.set_lang(saved_lang)

        self.title(i18n.t("app_title"))
        self.geometry("1000x760")
        self.minsize(760, 540)

        self.palette = theme.apply_theme(self)
        self._try_set_icon()

        self.analyzer: MissionAnalyzer | None = None
        self.tile_cache: OnlineTileCache | None = None
        self._map_images: list = []  # держим ссылки на PhotoImage, иначе GC их съест
        self._pil_warning_shown = False
        self._cancel_event: threading.Event | None = None
        self._map_loading = False  # флаг занятости загрузки тайлов (кнопок статуса больше нет)
        self._flight_conn = None   # активне з'єднання з польотним контролером (pymavlink/pyserial)

        # выбранный провайдер карты хранится как ключ (не как текст на экране) —
        # так переключение языка не ломает текущий выбор в комбобоксе
        self.provider_key = self._settings_data.get("provider_key", DEFAULT_PROVIDER_KEY)
        if self.provider_key not in PROVIDERS:
            self.provider_key = DEFAULT_PROVIDER_KEY

        self._init_vars()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(1500, lambda: self._check_for_updates(silent=True))

    # ---------------------------------------------------------- переменные --


    def _settings_path(self) -> str:
        base = _app_base_dir()
        return os.path.join(base, "settings.json")


    def _save_settings(self):
        data = {
            "lang": i18n.get_lang(),
            "srtm_dir": self.srtm_var.get(),
            "map_cache_dir": self.tilecache_var.get(),
            "alt_min": self.alt_min_var.get(),
            "turn_min": self.turn_min_var.get(),
            "zoom": self.zoom_var.get(),
            "provider_key": self.provider_key,
            "show_occupied": self.show_occupied_var.get(),
            "use_srtm": self.use_srtm_var.get(),
            "mission_file": self.file_var.get(),
            "flight_date": self.flight_date_var.get(),
            "flight_time": self.flight_time_var.get(),
            "cruise_speed": self.cruise_speed_var.get(),
            "url_occupied": self.url_occupied_var.get(),
            "url_windy": self.url_windy_var.get(),
            "url_forecast": self.url_forecast_var.get(),
            "url_gwa": self.url_gwa_var.get(),
        }
        settings.save_settings(self._settings_path(), data)


    def _on_close(self):
        self._save_settings()
        self.destroy()


    def _find_asset(self, names: tuple[str, ...]) -> str | None:
        base = _bundled_asset_dir()
        for name in names:
            path = os.path.join(base, name)
            if os.path.isfile(path):
                return path
        return None


    @staticmethod
    def _load_logo_thumbnail(path: str, target_h: int = 40):
        """Уменьшает логотип для шапки. С Pillow — плавно (LANCZOS), без него — грубее (subsample)."""
        try:
            from PIL import Image, ImageTk
            img = Image.open(path)
            ratio = target_h / img.height
            new_size = (max(1, int(img.width * ratio)), target_h)
            img = img.resize(new_size, Image.LANCZOS)
            return ImageTk.PhotoImage(img)
        except ImportError:
            pass
        except Exception:
            return None

        try:
            img = tk.PhotoImage(file=path)
            h = img.height()
            if h > target_h:
                factor = max(1, h // target_h)
                img = img.subsample(factor, factor)
            return img
        except tk.TclError:
            return None


    def _try_set_icon(self):
        """Ищет логотип рядом с программой (icon.png/logo.png/icon.ico/logo.ico) и ставит как иконку окна."""
        base = _bundled_asset_dir()
        for name in self.ICON_CANDIDATES:
            path = os.path.join(base, name)
            if os.path.isfile(path) and theme.set_window_icon(self, path):
                return


    def _init_vars(self):
        """
        Все Tk-переменные создаются один раз и не пересоздаются при смене
        языка/перестройке интерфейса — иначе введённые пользователем значения
        (путь к файлу, пороги и т.п.) терялись бы при каждом переключении.
        Начальные значения берутся из сохранённых настроек, если они есть.
        """
        d = self._settings_data
        self.file_var = tk.StringVar(value=d.get("mission_file", ""))
        self.alt_min_var = tk.StringVar(value=str(d.get("alt_min", "40")))
        self.turn_min_var = tk.StringVar(value=str(d.get("turn_min", "2")))
        self.use_srtm_var = tk.BooleanVar(value=d.get("use_srtm", True))
        self.srtm_var = tk.StringVar(value=d.get("srtm_dir", "srtm"))
        self.tilecache_var = tk.StringVar(value=d.get("map_cache_dir", "map_cache"))
        self.zoom_var = tk.IntVar(value=int(d.get("zoom", DEFAULT_ZOOM)))
        self.show_occupied_var = tk.BooleanVar(value=d.get("show_occupied", False))
        # дата і час планованого польоту (для аналізу метеоумов)
        self.flight_date_var = tk.StringVar(value=d.get("flight_date", ""))
        self.flight_time_var = tk.StringVar(value=d.get("flight_time", "12:00"))
        self.cruise_speed_var = tk.StringVar(value=str(d.get("cruise_speed", "50")))
        # URL-и картографічних і метеосервісів
        self.url_occupied_var = tk.StringVar(value=d.get("url_occupied", "https://deepstatemap.live/api/history/last/geojson"))
        self.url_windy_var = tk.StringVar(value=d.get("url_windy", "https://www.windy.com"))
        self.url_forecast_var = tk.StringVar(value=d.get("url_forecast", "https://open-meteo.com"))
        self.url_gwa_var = tk.StringVar(value=d.get("url_gwa", "https://globalwindatlas.info"))
        self.map_status_var = tk.StringVar(value="")
        self.occupied_status_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value=i18n.t("status_default"))

    # ------------------------------------------------------------------ UI --


    def _make_toggle_action_buttons(self, parent, specs: list[tuple[str, object]]) -> list[tk.Button]:
        """
        Пара кнопок-переключателей (Завантажити/Зберегти): нажатая
        становится чёрной и остаётся такой, пока не нажата другая -- та
        же логика, что и у кнопок навигации (_make_nav_button/_show_page:
        активная страница остаётся подсвеченной, пока не выбрана другая).
        specs -- список (текст, command).
        """
        colors = self.palette
        idle_bg, idle_fg = "#DEE3E8", colors["text"]
        idle_pad = (16, 8)
        active_bg, active_fg = colors["header_bg"], colors["text_light"]
        active_pad = (8, 3)
        border = colors["border"]

        buttons: list[tk.Button] = []

        def set_active(target: tk.Button):
            for b in buttons:
                if b is target:
                    b.configure(
                        bg=active_bg, fg=active_fg, padx=active_pad[0], pady=active_pad[1],
                        highlightbackground=colors["text_muted"], highlightcolor=colors["text_muted"],
                    )
                else:
                    b.configure(
                        bg=idle_bg, fg=idle_fg, padx=idle_pad[0], pady=idle_pad[1],
                        highlightbackground=border, highlightcolor=border,
                    )
            target.update_idletasks()

        for text, command in specs:
            btn = tk.Button(
                parent, text=text,
                bg=idle_bg, fg=idle_fg, activebackground="#C9CFD6", activeforeground=idle_fg,
                font=("Segoe UI", 9, "bold"), bd=2, relief="groove", cursor="hand2",
                padx=idle_pad[0], pady=idle_pad[1],
                highlightthickness=1, highlightbackground=border, highlightcolor=border,
            )
            buttons.append(btn)

        for btn, (_text, command) in zip(buttons, specs):
            def on_click(_event=None, btn=btn, command=command):
                set_active(btn)
                # небольшая пауза перед самим действием (открытие диалога
                # и т.п.), чтобы чёрный фон гарантированно успел
                # прорисоваться на экране до того, как что-то его перекроет
                btn.after(80, command)

            btn.bind("<Button-1>", on_click)

        return buttons


    def _make_nav_button(self, parent, icon_name: str, text: str, page_key: str) -> tk.Frame:
        """Кнопка навигации: иконка сверху (Canvas, рисуется векторно) + подпись снизу."""
        colors = self.palette
        frame = tk.Frame(parent, bg=colors["header_bg"], cursor="hand2")
        canvas = tk.Canvas(frame, width=26, height=26, bg=colors["header_bg"], highlightthickness=0)
        canvas.pack(padx=16, pady=(8, 2))
        label = tk.Label(
            frame, text=text, bg=colors["header_bg"], fg=colors["text_muted"],
            font=("Segoe UI", 9, "bold"),
        )
        label.pack(pady=(0, 8))

        def on_click(event=None):
            self._show_page(page_key)

        for widget in (frame, canvas, label):
            widget.bind("<Button-1>", on_click)

        frame._nav_canvas = canvas
        frame._nav_label = label
        frame._nav_icon = icon_name
        icons.draw_icon(canvas, icon_name, colors["text_muted"])
        return frame


    def _show_page(self, page_key: str):
        self._current_page = page_key
        page = self.pages.get(page_key)
        if page is not None:
            page.tkraise()

        if hasattr(self, "connect_box"):
            if page_key == "mission":
                self.connect_box.pack(fill="both", expand=True)
            else:
                self.connect_box.pack_forget()

        if hasattr(self, "analysis_save_box"):
            if page_key == "analysis":
                self.analysis_save_box.pack(fill="both", expand=True)
            else:
                self.analysis_save_box.pack_forget()

        colors = self.palette
        for key, btn in self.nav_buttons.items():
            active = key == page_key
            bg = colors["blue"] if active else colors["header_bg"]
            fg = colors["text_light"] if active else colors["text_muted"]
            btn.configure(bg=bg)
            btn._nav_canvas.configure(bg=bg)
            btn._nav_label.configure(bg=bg, fg=fg)
            icons.draw_icon(btn._nav_canvas, btn._nav_icon, fg)


    def _switch_language(self, lang_code: str):
        if i18n.get_lang() == lang_code:
            return
        i18n.set_lang(lang_code)
        self._save_settings()

        # запам'ятовуємо, чи були вкладки "Аналіз" реально показані (а не
        # плейсхолдер) -- щоб після перебудови UI відновити той самий
        # стан, а не рахувати важкі графіки/карту даремно, якщо їх і не
        # було видно
        tabs_were_visible = hasattr(self, "notebook") and self.notebook.winfo_ismapped()

        for child in self.winfo_children():
            child.destroy()

        # статусы карты завязаны на предыдущий рендер — после смены языка
        # холст карты всё равно пуст (пересоздан), так что сбрасываем
        self.map_status_var.set("")
        self.occupied_status_var.set("")

        self._build_ui()

        if self.analyzer is not None:
            self._distribute_report_text(self._captured_report())
            self.status_var.set(
                i18n.t("status_ready_fmt", n=len(self.analyzer.nav_wps), m=len(self.analyzer.issues))
            )
            self._analysis_built = False
            if tabs_were_visible:
                self._show_analysis_tabs()  # сам викличе _ensure_analysis_built()
            self._populate_mission_table(self.analyzer.all_wps)
            self.mission_content.tkraise()

    # ------------------------------------------------------------- обзоры --


    def _open_url(self, url: str):
        """Відкриває URL у браузері за замовчуванням."""
        import webbrowser
        if url:
            webbrowser.open(url)


    def _build_ui(self):
        pad = {"padx": 6, "pady": 4}

        header = ttk.Frame(self, style="Header.TFrame")
        header.pack(fill="x")

        header_inner = ttk.Frame(header, style="Header.TFrame")
        header_inner.pack(fill="x", padx=12, pady=8)

        logo_path = self._find_asset(("icon.png", "logo.png"))
        if logo_path:
            self._logo_img = self._load_logo_thumbnail(logo_path, target_h=40)
            if self._logo_img is not None:
                ttk.Label(header_inner, image=self._logo_img, style="Header.TFrame").pack(side="left", padx=(0, 10))

        title_box = ttk.Frame(header_inner, style="Header.TFrame")
        title_box.pack(side="left")
        ttk.Label(title_box, text=i18n.t("app_title"), style="Header.TLabel").pack(anchor="w")
        ttk.Label(title_box, text=i18n.t("app_subtitle"), style="HeaderSub.TLabel").pack(anchor="w")

        right_box = ttk.Frame(header_inner, style="Header.TFrame")
        right_box.pack(side="right")

        lang_box = ttk.Frame(right_box, style="Header.TFrame")
        lang_box.pack(anchor="e")
        for lang_code, label in (("uk", "UA"), ("en", "EN")):
            active = i18n.get_lang() == lang_code
            btn = ttk.Button(
                lang_box, text=label, width=4,
                style="LangToggleActive.TButton" if active else "LangToggle.TButton",
                command=lambda lc=lang_code: self._switch_language(lc),
            )
            btn.pack(side="left", padx=2)

        # --- підключення до польотного контролера (тільки на сторінці
        # "Місія") -- порт / швидкість обміну / кнопка, як у Mission Planner
        # --- та збереження звіту аналізу в PDF (тільки на сторінці "Аналіз") --
        #
        # Обидва блоки живуть у СПІЛЬНОМУ "слоті" фіксованої висоти
        # (_header_right_slot, pack_propagate(False)) замість того, щоб
        # пакуватись/ховатись напряму в right_box. Якщо ховати напряму --
        # right_box (і вся шапка) природно "стискається" по висоті на
        # сторінках "Конфігурація"/"Довідка", де обидва блоки сховані,
        # і шапка стрибає між сторінками. Слот завжди займає однакову
        # висоту (по більшому з двох блоків), навіть коли всередині
        # порожньо.
        self._header_right_slot = ttk.Frame(right_box, style="Header.TFrame")

        self.connect_box = ttk.Frame(self._header_right_slot, style="Header.TFrame")
        self._build_connect_bar(self.connect_box)

        self.analysis_save_box = ttk.Frame(self._header_right_slot, style="Header.TFrame")
        self._build_analysis_save_button(self.analysis_save_box)

        slot_h = max(self.connect_box.winfo_reqheight(), self.analysis_save_box.winfo_reqheight())
        self._header_right_slot.configure(height=slot_h)
        self._header_right_slot.pack_propagate(False)
        self._header_right_slot.pack(anchor="e", pady=(6, 0))

        # --- навигационная панель: 4 кнопки (иконка сверху + подпись) ---
        navbar = tk.Frame(self, bg=self.palette["header_bg"])
        navbar.pack(fill="x")
        self.nav_buttons = {}
        for page_key, label_key, icon_name in (
            ("mission", "nav_mission", "mission"),
            ("analysis", "nav_analysis", "analysis"),
            ("config", "nav_config", "config"),
            ("help", "nav_help", "help"),
        ):
            btn = self._make_nav_button(navbar, icon_name, i18n.t(label_key), page_key)
            btn.pack(side="left")
            self.nav_buttons[page_key] = btn

        # --- контейнер страниц: все страницы занимают одну и ту же ячейку,
        # видна только поднятая наверх (tkraise) -- resize окна не ломает
        # раскладку, т.к. это обычный grid/pack, а не абсолютные координаты
        content = ttk.Frame(self)
        content.pack(fill="both", expand=True)
        content.rowconfigure(0, weight=1)
        content.columnconfigure(0, weight=1)
        self.pages = {}

        # каждая страница строится соответствующим миксином — App лишь
        # передаёт общий контейнер content и стандартные отступы pad
        self._build_mission_page(content, pad)
        self._build_analysis_page(content, pad)
        self._build_config_page(content, pad)
        self._build_help_page(content, pad)

        status_bar = ttk.Frame(self, style="Status.TFrame")
        status_bar.pack(fill="x", side="bottom")
        ttk.Label(status_bar, textvariable=self.status_var, style="Status.TLabel", anchor="w").pack(
            fill="x", padx=10, pady=4
        )

        self._show_page(getattr(self, "_current_page", "mission"))

def main():
    App().mainloop()


if __name__ == "__main__":
    main()
