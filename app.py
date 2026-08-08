"""
app.py — GUI-оболочка Mission Analyzer. Сама ничего не анализирует и не
парсит .waypoints — только собирает интерфейс и вызывает модули:
waypoints.parse_waypoints, analyzer.MissionAnalyzer, srtm.SRTMTerrain,
online_tiles.OnlineTileCache, elevation_view.draw_elevation_profile,
map_view.render_tiles.

Локализация — через i18n.py (украинский/английский, без русского).
"""

from __future__ import annotations

import io
import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from waypoints import parse_waypoints, command_name

# MAV_FRAME -> читабельна назва висоти (як у Mission Planner)
_MAV_FRAME_NAMES = {
    0: "Absolute",
    1: "Above Home",
    2: "Local NED",
    3: "Mission",
    4: "Global Rel. Alt",  # GLOBAL_RELATIVE_ALT -- найпоширеніший
    5: "Local ENU",
    6: "Global Int",
    7: "Global Rel. Alt Int",
    10: "Local Offset NED",
    11: "Body NED",
    12: "Body Offset NED",
    13: "Global Terrain Alt",   # Terrain
    14: "Global Terrain Alt Int",
}

def _frame_name(frame: int) -> str:
    """Повертає текстову назву MAV_FRAME для колонки таблиці місії."""
    return _MAV_FRAME_NAMES.get(int(frame), f"Frame {frame}")
from geo import haversine_m, bearing_deg
from srtm import SRTMTerrain, SRTMError
from online_tiles import OnlineTileCache, PROVIDERS
from analyzer import MissionAnalyzer
from elevation_view import draw_elevation_profile, draw_takeoff_profile
from angle_view import draw_angle_profile
from landing_view import draw_landing_approach
from map_view import (compute_tile_bounds, fetch_tiles, render_tiles, bind_pan,
                      MapTooLargeError, compute_area_tile_bounds, render_area_map)
from occupied_layer import fetch_occupied_geojson, extract_polygons
import i18n
import theme
import icons
import settings
import version
import changelog

DEFAULT_ZOOM = 9
DEFAULT_PROVIDER_KEY = "google_hybrid"


class App(tk.Tk):
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

    # ---------------------------------------------------------- переменные --

    def _settings_path(self) -> str:
        base = os.path.dirname(os.path.abspath(__file__))
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
        base = os.path.dirname(os.path.abspath(__file__))
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
        base = os.path.dirname(os.path.abspath(__file__))
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
        self.connect_box = ttk.Frame(right_box, style="Header.TFrame")
        self._build_connect_bar(self.connect_box)

        # --- збереження звіту аналізу в PDF (тільки на сторінці "Аналіз") ---
        self.analysis_save_box = ttk.Frame(right_box, style="Header.TFrame")
        self._build_analysis_save_button(self.analysis_save_box)

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

        # === страница "Місія" ===
        page_mission = ttk.Frame(content)
        page_mission.grid(row=0, column=0, sticky="nsew")
        self.pages["mission"] = page_mission

        btns = ttk.Frame(page_mission)
        btns.pack(fill="x", **pad)
        load_btn, save_btn = self._make_toggle_action_buttons(
            btns, [(i18n.t("btn_load"), self.load_mission), (i18n.t("btn_save"), self.save_csv)]
        )
        load_btn.pack(side="left")
        save_btn.pack(side="left", padx=6)

        # кнопки Read/Write для ArduPilot -- видимі тільки коли підключено
        self._ardu_read_btn, self._ardu_write_btn = self._make_toggle_action_buttons(
            btns, [("Read", self._load_mission_from_mavlink), ("Write", self._save_mission_to_mavlink)]
        )
        # спочатку сховані -- покажемо при підключенні
        self._ardu_btns_visible = False

        # тело страницы -- либо чёрный плейсхолдер с лого (пока ничего не
        # загружено), либо таблица+карта (после успешной загрузки миссии).
        # Пустая таблица/карта до загрузки не несут смысла, поэтому вместо
        # них показываем то же самое, что и на сплэш-экране при старте.
        mission_body = ttk.Frame(page_mission)
        mission_body.pack(fill="both", expand=True)
        mission_body.rowconfigure(0, weight=1)
        mission_body.columnconfigure(0, weight=1)

        self.mission_placeholder = tk.Frame(mission_body, bg="black")
        self.mission_placeholder.grid(row=0, column=0, sticky="nsew")
        logo_path = self._find_asset(("icon.png", "logo.png"))
        if logo_path:
            self._mission_placeholder_logo = self._load_logo_thumbnail(logo_path, target_h=170)
            if self._mission_placeholder_logo is not None:
                tk.Label(self.mission_placeholder, image=self._mission_placeholder_logo, bg="black").place(
                    relx=0.5, rely=0.5, anchor="center"
                )

        self.mission_content = ttk.Frame(mission_body)
        self.mission_content.grid(row=0, column=0, sticky="nsew")

        table_frame = ttk.Frame(self.mission_content)
        table_frame.pack(fill="x", **pad)
        table_columns = ("idx", "cmd", "p1", "p2", "p3", "p4", "lat", "lon", "alt", "frame", "dist", "az")
        self.mission_table = ttk.Treeview(
            table_frame, columns=table_columns, show="headings", height=7,
        )
        table_headings = {
            "idx": ("table_col_idx", 36),
            "cmd": ("table_col_command", 130),
            "p1": ("table_col_p1", 55),
            "p2": ("table_col_p2", 55),
            "p3": ("table_col_p3", 55),
            "p4": ("table_col_p4", 55),
            "lat": ("table_col_lat", 90),
            "lon": ("table_col_lon", 90),
            "alt": ("table_col_alt", 60),
            "frame": ("table_col_frame", 50),
            "dist": ("table_col_dist", 70),
            "az": ("table_col_az", 55),
        }
        for col, (key, width) in table_headings.items():
            self.mission_table.heading(col, text=i18n.t(key))
            self.mission_table.column(col, width=width, anchor="center", stretch=False)
        table_vbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.mission_table.yview)
        self.mission_table.configure(yscrollcommand=table_vbar.set)
        self.mission_table.pack(side="left", fill="x", expand=True)
        table_vbar.pack(side="left", fill="y")

        map_ctrl2 = ttk.Frame(self.mission_content)
        map_ctrl2.pack(fill="x", padx=6)
        ttk.Label(map_ctrl2, textvariable=self.occupied_status_var, foreground="#555").pack(side="left")

        map_canvas_frame = ttk.Frame(self.mission_content)
        map_canvas_frame.pack(fill="both", expand=True, **pad)
        map_canvas_frame.rowconfigure(0, weight=1)
        map_canvas_frame.columnconfigure(0, weight=1)

        self.map_canvas = tk.Canvas(map_canvas_frame, bg="#dddddd")
        map_vbar = ttk.Scrollbar(map_canvas_frame, orient="vertical", command=self.map_canvas.yview)
        map_hbar = ttk.Scrollbar(map_canvas_frame, orient="horizontal", command=self.map_canvas.xview)
        self.map_canvas.configure(yscrollcommand=map_vbar.set, xscrollcommand=map_hbar.set)

        self.map_canvas.grid(row=0, column=0, sticky="nsew")
        map_vbar.grid(row=0, column=1, sticky="ns")
        map_hbar.grid(row=1, column=0, sticky="ew")

        bind_pan(self.map_canvas)
        self.map_canvas.bind("<MouseWheel>", self._on_map_wheel)     # Windows / macOS
        self.map_canvas.bind("<Button-4>", self._on_map_wheel)       # Linux — колесо вверх
        self.map_canvas.bind("<Button-5>", self._on_map_wheel)       # Linux — колесо вниз

        # по умолчанию -- плейсхолдер; если миссия уже была загружена до
        # перестройки интерфейса (например, при смене языка) -- ниже, после
        # полной сборки страниц, покажем контент вместо него
        self.mission_placeholder.tkraise()

        # === страница "Аналіз" ===
        page_analysis = ttk.Frame(content)
        page_analysis.grid(row=0, column=0, sticky="nsew")
        self.pages["analysis"] = page_analysis

        # рядок дати/часу планованого польоту -- вгорі, над вкладками
        flight_row = ttk.Frame(page_analysis)
        flight_row.pack(fill="x", **pad)

        ttk.Label(flight_row, text="Дата польоту:").pack(side="left")
        self._date_btn = tk.Button(
            flight_row,
            textvariable=self.flight_date_var,
            font=("Segoe UI", 9, "bold"),
            bg="#DEE3E8", fg=self.palette["text"],
            bd=2, relief="groove", cursor="hand2", padx=8, pady=3,
            highlightthickness=1,
            highlightbackground=self.palette["border"],
            command=self._pick_date,
        )
        self._date_btn.pack(side="left", padx=(4, 16))
        if not self.flight_date_var.get():
            import datetime
            self.flight_date_var.set(datetime.date.today().strftime("%Y-%m-%d"))

        ttk.Label(flight_row, text="Час вильоту (UTC):").pack(side="left")
        hours = [f"{h:02d}:00" for h in range(24)]
        hour_combo = ttk.Combobox(
            flight_row, textvariable=self.flight_time_var,
            values=hours, width=6, state="readonly",
        )
        hour_combo.pack(side="left", padx=(4, 16))

        ttk.Label(flight_row, text="Прибуття (UTC):").pack(side="left")
        self.arrival_time_var = tk.StringVar(value="—")
        ttk.Label(flight_row, textvariable=self.arrival_time_var,
                  font=("Segoe UI", 9, "bold")).pack(side="left", padx=(4, 20))

        ttk.Button(
            flight_row, text="Отримати метео",
            command=self._fetch_meteo,
        ).pack(side="left")

        self.notebook = ttk.Notebook(page_analysis)
        self.notebook.pack(fill="both", expand=True, **pad)

        self._meteo_canvases = []          # [0]=Зліт(старт), [1]=Глісада(посадка)
        self._meteo_map_images = [[], []]  # тримаємо refs до PhotoImage
        self._meteo_render_params = [None, None]  # кеш параметрів останнього рендеру -- для перемальовки при <Configure>
        self._glide_issues_text = ""       # текст проблем глісади зі звіту (наповнюється при завантаженні місії)
        self._land_weather_text = ""       # текст погоди для посадки (наповнюється по кнопці "Отримати метео")

        def make_scroll_tab(tab_title: str):
            """Один вертикальний скрол на всю вкладку -- контент іде
            суцільним стовпцем зверху вниз, ніяких вкладених панелей.
            Повертає (tab_frame, inner_frame)."""
            tab = ttk.Frame(self.notebook)
            self.notebook.add(tab, text=tab_title)

            outer = tk.Canvas(tab, highlightthickness=0, bg=self.palette["bg"])
            vbar = ttk.Scrollbar(tab, orient="vertical", command=outer.yview)
            outer.configure(yscrollcommand=vbar.set)
            vbar.pack(side="right", fill="y")
            outer.pack(side="left", fill="both", expand=True)

            inner = ttk.Frame(outer)
            inner_id = outer.create_window((0, 0), window=inner, anchor="nw")

            def _on_inner_configure(_e=None):
                outer.configure(scrollregion=outer.bbox("all"))

            def _on_outer_configure(event):
                outer.itemconfig(inner_id, width=event.width)

            inner.bind("<Configure>", _on_inner_configure)
            outer.bind("<Configure>", _on_outer_configure)

            def _on_wheel(event):
                outer.yview_scroll(int(-1 * (event.delta / 120)), "units")

            outer.bind("<MouseWheel>", _on_wheel)
            inner.bind("<MouseWheel>", _on_wheel)

            return tab, inner

        def add_map_block(parent, map_title: str, height: int = 460):
            """Карта 4×4 км -- КВАДРАТНА (висота = ширині, підлаштовується
            при зміні розміру вікна). Панорамування -- перетягуванням миші
            (bind_pan), без окремих смуг прокрутки: на вкладці має бути
            лише один спільний вертикальний повзунок."""
            map_box = ttk.LabelFrame(parent, text=map_title, height=height)
            map_box.pack(fill="x", expand=False, pady=(0, 8))
            map_box.pack_propagate(False)

            def _keep_square(event, _box=map_box):
                if event.width > 10:
                    _box.configure(height=event.width)

            map_box.bind("<Configure>", _keep_square)

            canvas = tk.Canvas(map_box, bg="#cccccc", highlightthickness=0)
            canvas.pack(fill="both", expand=True)
            bind_pan(canvas)
            idx = len(self._meteo_canvases)
            self._meteo_canvases.append(canvas)

            def _on_canvas_configure(event, _idx=idx):
                params = self._meteo_render_params[_idx]
                if params is None:
                    return
                render_area_map(canvas, *params)

            canvas.bind("<Configure>", _on_canvas_configure)
            return canvas

        def make_plain_text(parent, height: int):
            """Звичайний tk.Text БЕЗ власної смуги прокрутки -- на вкладці
            має бути лише один спільний вертикальний повзунок (від
            make_scroll_tab), а не по одному на кожен текстовий блок."""
            return tk.Text(
                parent, wrap="word", font=("Consolas", 9), state="disabled",
                height=height, relief="solid", borderwidth=1,
            )

        # --- «Зліт» = текст (погода), карта, профіль висоти зльоту ---
        takeoff_tab, takeoff_inner = make_scroll_tab("Зліт")
        self.takeoff_weather_text = make_plain_text(takeoff_inner, height=8)
        self.takeoff_weather_text.pack(fill="x", pady=(0, 8))
        add_map_block(takeoff_inner, "Старт — 4×4 км")

        takeoff_profile_box = ttk.LabelFrame(takeoff_inner, text="Профіль висоти — зліт")
        takeoff_profile_box.pack(fill="x", pady=(0, 8))
        self.takeoff_profile_canvas = tk.Canvas(takeoff_profile_box, bg="white", height=280)
        self.takeoff_profile_canvas.pack(fill="x")
        self.takeoff_profile_canvas.bind("<Configure>", lambda e: self._redraw_takeoff_profile())

        # --- «Траєкторія» = текст (звіти висоти+кута), карта маршруту, профілі (висота+кут) ---
        trajectory_tab, trajectory_inner = make_scroll_tab("Траєкторія")

        traj_text_box = ttk.LabelFrame(trajectory_inner, text="Звіт")
        traj_text_box.pack(fill="x", pady=(0, 8))
        ttk.Label(traj_text_box, text=i18n.t("tab_elevation")).pack(anchor="w", padx=4)
        self.elev_report_text = make_plain_text(traj_text_box, height=5)
        self.elev_report_text.pack(fill="x", padx=4, pady=(0, 4))
        ttk.Label(traj_text_box, text=i18n.t("tab_angle")).pack(anchor="w", padx=4)
        self.angle_report_text = make_plain_text(traj_text_box, height=5)
        self.angle_report_text.pack(fill="x", padx=4, pady=(0, 4))

        traj_map_box = ttk.LabelFrame(trajectory_inner, text="Маршрут — вигляд згори")
        traj_map_box.pack(fill="x", pady=(0, 8))
        traj_map_box.pack_propagate(False)

        def _keep_square_traj(event, _box=traj_map_box):
            if event.width > 10:
                _box.configure(height=event.width)

        traj_map_box.bind("<Configure>", _keep_square_traj)

        self.trajectory_map_canvas = tk.Canvas(traj_map_box, bg="#cccccc", highlightthickness=0)
        self.trajectory_map_canvas.pack(fill="both", expand=True)
        bind_pan(self.trajectory_map_canvas)
        self._trajectory_map_params = None  # кеш (tiles, zoom, bounds) -- для перемальовки без повторного фетчу

        def _on_traj_map_configure(event):
            if self._trajectory_map_params is None:
                return
            tiles, zoom, tx_min, tx_max, ty_min, ty_max = self._trajectory_map_params
            render_tiles(
                self.trajectory_map_canvas, self.analyzer, zoom,
                tx_min, tx_max, ty_min, ty_max, tiles, self._trajectory_map_images,
            )

        self._trajectory_map_images = []
        self.trajectory_map_canvas.bind("<Configure>", _on_traj_map_configure)

        elev_box = ttk.LabelFrame(trajectory_inner, text=i18n.t("tab_elevation"))
        elev_box.pack(fill="x", pady=(0, 8))
        self.plot_canvas = tk.Canvas(elev_box, bg="white", height=320)
        self.plot_canvas.pack(fill="x")
        self.plot_canvas.bind("<Configure>", lambda e: self._redraw_plot())

        angle_box = ttk.LabelFrame(trajectory_inner, text=i18n.t("tab_angle"))
        angle_box.pack(fill="x", pady=(0, 8))
        self.angle_canvas = tk.Canvas(angle_box, bg="white", height=320)
        self.angle_canvas.pack(fill="x")
        self.angle_canvas.bind("<Configure>", lambda e: self._redraw_angle_plot())

        # --- «Глісада» = звіт+погода, потім карта, потім графік глісади ---
        landing_tab, landing_inner = make_scroll_tab("Глісада")
        self.glide_report_text = make_plain_text(landing_inner, height=8)
        self.glide_report_text.pack(fill="x", pady=(0, 8))
        add_map_block(landing_inner, "Посадка — 4×4 км")
        landing_chart_box = ttk.LabelFrame(landing_inner, text="Графік глісади")
        landing_chart_box.pack(fill="x", pady=(0, 8))
        self.landing_canvas = tk.Canvas(landing_chart_box, bg="white", height=300)
        self.landing_canvas.pack(fill="x")
        self.landing_canvas.bind("<Configure>", lambda e: self._redraw_landing_plot())


        # === страница "Конфігурація" ===
        page_config = ttk.Frame(content)
        page_config.grid(row=0, column=0, sticky="nsew")
        self.pages["config"] = page_config

        opts = ttk.LabelFrame(page_config, text=i18n.t("label_params"))
        opts.pack(fill="x", **pad)

        ttk.Label(opts, text=i18n.t("label_alt_min")).grid(row=0, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(opts, textvariable=self.alt_min_var, width=8).grid(row=0, column=1, sticky="w")

        ttk.Label(opts, text=i18n.t("label_turn_min")).grid(row=0, column=2, sticky="w", padx=(16, 6))
        ttk.Entry(opts, textvariable=self.turn_min_var, width=8).grid(row=0, column=3, sticky="w")

        ttk.Label(opts, text="Крейсерська швидкість (м/с):").grid(row=0, column=4, sticky="w", padx=(16, 6))
        ttk.Entry(opts, textvariable=self.cruise_speed_var, width=6).grid(row=0, column=5, sticky="w")

        ttk.Checkbutton(opts, text=i18n.t("check_srtm"), variable=self.use_srtm_var).grid(
            row=1, column=0, sticky="w", padx=6, pady=(2, 4)
        )
        ttk.Entry(opts, textvariable=self.srtm_var).grid(row=1, column=1, columnspan=2, sticky="we", padx=4)
        ttk.Button(opts, text=i18n.t("btn_browse"), command=self.browse_srtm).grid(row=1, column=3, sticky="w", padx=6)

        ttk.Label(opts, text=i18n.t("label_map_cache")).grid(row=2, column=0, sticky="w", padx=6, pady=(2, 4))
        ttk.Entry(opts, textvariable=self.tilecache_var).grid(row=2, column=1, columnspan=2, sticky="we", padx=4)
        ttk.Button(opts, text=i18n.t("btn_browse"), command=self.browse_tilecache).grid(row=2, column=3, sticky="w", padx=6)

        opts.columnconfigure(1, weight=1)
        opts.columnconfigure(2, weight=1)

        map_opts = ttk.LabelFrame(page_config, text=i18n.t("label_map_settings"))
        map_opts.pack(fill="x", **pad)

        ttk.Label(map_opts, text=i18n.t("label_map_provider")).grid(row=0, column=0, sticky="w", padx=6, pady=4)

        self._provider_names = {}   # display_name -> key (для текущего языка)
        provider_display_names = []
        current_display = None
        for key, info in PROVIDERS.items():
            display = i18n.t(f"provider_{key}")
            self._provider_names[display] = key
            provider_display_names.append(display)
            if key == self.provider_key:
                current_display = display

        self.provider_var = tk.StringVar(value=current_display or provider_display_names[0])
        provider_box = ttk.Combobox(
            map_opts, textvariable=self.provider_var, state="readonly",
            values=provider_display_names, width=28,
        )
        provider_box.grid(row=0, column=1, sticky="w", padx=4)
        provider_box.bind("<<ComboboxSelected>>", self._on_provider_selected)

        ttk.Checkbutton(
            map_opts, text=i18n.t("check_occupied"), variable=self.show_occupied_var,
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=6, pady=(2, 4))

        ttk.Label(map_opts, text=i18n.t("label_zoom")).grid(row=2, column=0, sticky="w", padx=6, pady=(2, 6))
        ttk.Spinbox(map_opts, from_=1, to=19, textvariable=self.zoom_var, width=4).grid(
            row=2, column=1, sticky="w", padx=4, pady=(2, 6)
        )

        # === Картографічні та метеосервіси ===
        svc_frame = ttk.LabelFrame(page_config, text="Картографічні та метеосервіси")
        svc_frame.pack(fill="x", **pad)

        services = [
            ("Шар окупованих територій:", self.url_occupied_var),
            ("Windy (вітер, онлайн-карта):", self.url_windy_var),
            ("Open-Meteo (прогноз, безкоштовно):", self.url_forecast_var),
            ("Global Wind Atlas (кліматика):", self.url_gwa_var),
        ]
        for row_i, (label, var) in enumerate(services):
            ttk.Label(svc_frame, text=label).grid(row=row_i, column=0, sticky="w", padx=6, pady=3)
            ttk.Entry(svc_frame, textvariable=var).grid(
                row=row_i, column=1, sticky="we", padx=(4, 6), pady=3
            )
            ttk.Button(
                svc_frame, text="↗",
                command=lambda u=var: self._open_url(u.get()),
                width=3,
            ).grid(row=row_i, column=2, padx=(0, 6), pady=3)

        svc_frame.columnconfigure(1, weight=1)

        # === страница "Довідка" ===
        page_help = ttk.Frame(content)
        page_help.grid(row=0, column=0, sticky="nsew")
        self.pages["help"] = page_help

        help_notebook = ttk.Notebook(page_help)
        help_notebook.pack(fill="both", expand=True, padx=10, pady=10)

        help_tab = ttk.Frame(help_notebook)
        help_notebook.add(help_tab, text=i18n.t("tab_help"))
        help_text = scrolledtext.ScrolledText(help_tab, wrap="word", font=("Segoe UI", 10))
        help_text.pack(fill="both", expand=True)
        help_text.insert("end", i18n.t("help_text_body"))
        help_text.config(state="disabled")

        changelog_tab = ttk.Frame(help_notebook)
        help_notebook.add(changelog_tab, text=i18n.t("tab_changelog"))
        changelog_text = scrolledtext.ScrolledText(changelog_tab, wrap="word", font=("Segoe UI", 10))
        changelog_text.pack(fill="both", expand=True)
        changelog_text.insert("end", f"{i18n.t('app_title')} — {i18n.t('label_version')} {version.VERSION}")
        changelog_text.insert("end", changelog.format_changelog(i18n.get_lang()))
        changelog_text.config(state="disabled")

        status_bar = ttk.Frame(self, style="Status.TFrame")
        status_bar.pack(fill="x", side="bottom")
        ttk.Label(status_bar, textvariable=self.status_var, style="Status.TLabel", anchor="w").pack(
            fill="x", padx=10, pady=4
        )

        self._show_page(getattr(self, "_current_page", "mission"))

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

    def _build_analysis_save_button(self, parent: ttk.Frame):
        """Кнопка «Зберегти» на сторінці «Аналіз» -- зберігає весь звіт
        аналізу (Зліт/Траєкторія/Глісада) в PDF."""
        colors = self.palette
        idle_bg, idle_fg = "#DEE3E8", colors["text"]
        border = colors["border"]

        self.analysis_save_btn = tk.Button(
            parent, text="Зберегти PDF",
            bg=idle_bg, fg=idle_fg, activebackground="#C9CFD6", activeforeground=idle_fg,
            font=("Segoe UI", 9, "bold"), bd=2, relief="groove", cursor="hand2",
            padx=16, pady=6,
            highlightthickness=1, highlightbackground=border, highlightcolor=border,
            command=self._save_analysis_pdf,
        )
        self.analysis_save_btn.pack(side="left")

    def _save_analysis_pdf(self):
        if self.analyzer is None:
            messagebox.showwarning(i18n.t("msg_no_data_title"), i18n.t("msg_no_data_body"))
            return

        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.units import mm
            from reportlab.pdfgen import canvas as pdfcanvas
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont
        except ImportError:
            messagebox.showerror(
                "PDF",
                "Для збереження в PDF потрібна бібліотека reportlab.\n\n"
                "Встановіть її командою:\n    pip install reportlab",
            )
            return

        path = filedialog.asksaveasfilename(
            title="Зберегти звіт аналізу",
            defaultextension=".pdf",
            filetypes=[("PDF", "*.pdf")],
        )
        if not path:
            return

        try:
            self._render_analysis_pdf(path, pdfcanvas, A4, mm, pdfmetrics, TTFont)
        except Exception as e:
            messagebox.showerror("PDF", f"Не вдалося зберегти PDF:\n{e}")
            return

        messagebox.showinfo("PDF", f"Звіт збережено:\n{path}")

    def _render_analysis_pdf(self, path, pdfcanvas, A4, mm, pdfmetrics, TTFont):
        """Формує PDF зі звітом: Зліт (погода), Траєкторія (висота+кут),
        Глісада (проблеми+погода). Текстовий звіт -- без растеризації
        карт/графіків (це окремі tk.Canvas, для їх експорту знадобився б
        Ghostscript чи подібне -- зайва залежність)."""
        # шрифт з підтримкою кирилиці, якщо є в системі; інакше -- вбудований
        font_name = "Helvetica"
        for candidate in ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/calibri.ttf"):
            if os.path.exists(candidate):
                try:
                    pdfmetrics.registerFont(TTFont("BodyFont", candidate))
                    font_name = "BodyFont"
                    break
                except Exception:
                    pass

        c = pdfcanvas.Canvas(path, pagesize=A4)
        page_w, page_h = A4
        margin = 18 * mm
        y = page_h - margin
        line_h = 4.6 * mm

        def new_page():
            nonlocal y
            c.showPage()
            c.setFont(font_name, 9)
            y = page_h - margin

        def write_title(text, size=14):
            nonlocal y
            c.setFont(font_name, size)
            c.drawString(margin, y, text)
            y -= size * 0.6 * mm + line_h

        def write_heading(text):
            nonlocal y
            if y < margin + 20 * mm:
                new_page()
            c.setFont(font_name, 11)
            c.drawString(margin, y, text)
            y -= line_h * 1.3

        def write_body(text: str):
            nonlocal y
            c.setFont(font_name, 9)
            for raw_line in text.split("\n"):
                # проста обгортка по ширині сторінки
                max_chars = 100
                line = raw_line if raw_line else " "
                while len(line) > max_chars:
                    cut = line.rfind(" ", 0, max_chars)
                    cut = cut if cut > 0 else max_chars
                    if y < margin:
                        new_page()
                    c.drawString(margin, y, line[:cut])
                    y -= line_h
                    line = line[cut:].lstrip()
                if y < margin:
                    new_page()
                c.drawString(margin, y, line)
                y -= line_h

        c.setFont(font_name, 9)
        write_title("Звіт аналізу місії — Mission Analyzer")
        write_body(f"Файл місії: {self.file_var.get() or '—'}")
        write_body(f"Дата польоту: {self.flight_date_var.get() or '—'}   "
                   f"Час вильоту (UTC): {self.flight_time_var.get() or '—'}   "
                   f"Прибуття: {self.arrival_time_var.get() if hasattr(self, 'arrival_time_var') else '—'}")
        y -= line_h

        write_heading("Зліт — погода в точці старту")
        write_body(self._get_text(self.takeoff_weather_text) or "Немає даних (натисніть «Отримати метео»).")
        y -= line_h

        write_heading("Траєкторія — графік висоти")
        write_body(self._get_text(self.elev_report_text) or "Без зауважень.")
        y -= line_h

        write_heading("Траєкторія — кут траєкторії")
        write_body(self._get_text(self.angle_report_text) or "Без зауважень.")
        y -= line_h

        write_heading("Глісада — проблеми та погода посадки")
        write_body(self._get_text(self.glide_report_text) or "Без зауважень.")

        c.save()

    @staticmethod
    def _get_text(widget) -> str:
        return widget.get("1.0", "end").rstrip()

    def _build_connect_bar(self, parent: ttk.Frame):
        """
        Порт / швидкість обміну / кнопка "Підєднатись" -- як у Mission
        Planner. Поля без підписів, вибір лише випадаючим списком.
        """
        colors = self.palette

        self.port_var = tk.StringVar()
        self.baud_var = tk.StringVar(value="115200")

        ports = self._list_serial_ports()
        port_combo = ttk.Combobox(
            parent, textvariable=self.port_var, values=ports, width=10,
            state="readonly" if ports else "normal",
        )
        if ports:
            self.port_var.set(ports[0])
        port_combo.pack(side="left", padx=(0, 4))

        baud_combo = ttk.Combobox(
            parent, textvariable=self.baud_var, width=7, state="readonly",
            values=["4800", "9600", "19200", "38400", "57600", "115200", "230400"],
        )
        baud_combo.pack(side="left", padx=(0, 6))

        idle_bg, idle_fg = "#DEE3E8", colors["text"]
        idle_pad = (16, 6)
        active_bg, active_fg = colors["header_bg"], colors["text_light"]
        active_pad = (8, 2)
        border = colors["border"]

        self.connect_btn = tk.Button(
            parent, text="Підєднатись",
            bg=idle_bg, fg=idle_fg, activebackground="#C9CFD6", activeforeground=idle_fg,
            font=("Segoe UI", 9, "bold"), bd=2, relief="groove", cursor="hand2",
            padx=idle_pad[0], pady=idle_pad[1],
            highlightthickness=1, highlightbackground=border, highlightcolor=border,
            command=self._toggle_flight_connection,
        )
        self.connect_btn.pack(side="left")

        self._connect_idle_style = dict(bg=idle_bg, fg=idle_fg, padx=idle_pad[0], pady=idle_pad[1])
        self._connect_active_style = dict(bg=active_bg, fg=active_fg, padx=active_pad[0], pady=active_pad[1])

    @staticmethod
    def _list_serial_ports() -> list[str]:
        try:
            import serial.tools.list_ports as list_ports
        except ImportError:
            return []
        try:
            return [p.device for p in list_ports.comports()]
        except Exception:
            return []

    def _toggle_flight_connection(self):
        if self._flight_conn is not None:
            self._disconnect_flight_controller()
            return

        port = self.port_var.get().strip()
        baud_txt = self.baud_var.get().strip()
        if not port:
            messagebox.showwarning(i18n.t("msg_no_data_title"), "Оберіть порт підключення")
            return
        try:
            baud = int(baud_txt)
        except ValueError:
            messagebox.showwarning(i18n.t("msg_no_data_title"), "Некоректна швидкість обміну")
            return

        self.connect_btn.configure(text="Підключення...", state="disabled")
        threading.Thread(target=self._connect_worker, args=(port, baud), daemon=True).start()

    def _connect_worker(self, port: str, baud: int):
        """Фоновий поток: тут можна безпечно чекати на heartbeat, не підвішуючи вікно."""
        conn = None
        error = None
        try:
            from pymavlink import mavutil
            conn = mavutil.mavlink_connection(port, baud=baud)
            conn.wait_heartbeat(timeout=10)
        except ImportError:
            # pymavlink не встановлено -- пробуємо хоча б просто відкрити порт
            try:
                import serial
                conn = serial.Serial(port, baud, timeout=2)
            except Exception as e:
                error = str(e)
        except Exception as e:
            error = str(e)
        self.after(0, lambda: self._on_connect_result(conn, error, port, baud))

    def _on_connect_result(self, conn, error, port: str, baud: int):
        if conn is None or error:
            self.connect_btn.configure(text="Підєднатись", state="normal", **self._connect_idle_style)
            messagebox.showerror(
                "MAVLink",
                f"Не вдалося підключитись до {port} @ {baud}"
                + (f":\n{error}" if error else ""),
            )
            return

        self._flight_conn = conn
        self.connect_btn.configure(text="Роз'єднати", state="normal", **self._connect_active_style)
        self.connect_btn.update_idletasks()
        self.status_var.set(f"Підключено: {port} @ {baud}")
        # показуємо кнопки Read/Write
        if hasattr(self, "_ardu_read_btn"):
            self._ardu_read_btn.pack(side="left", padx=(18, 0))
            self._ardu_write_btn.pack(side="left", padx=6)
            self._ardu_btns_visible = True

    def _disconnect_flight_controller(self):
        if self._flight_conn is not None:
            try:
                self._flight_conn.close()
            except Exception:
                pass
            self._flight_conn = None
        self.connect_btn.configure(text="Підєднатись", state="normal", **self._connect_idle_style)
        self.status_var.set("")
        # ховаємо кнопки Read/Write
        if hasattr(self, "_ardu_read_btn") and self._ardu_btns_visible:
            self._ardu_read_btn.pack_forget()
            self._ardu_write_btn.pack_forget()
            self._ardu_btns_visible = False

    def _show_page(self, page_key: str):
        self._current_page = page_key
        page = self.pages.get(page_key)
        if page is not None:
            page.tkraise()

        if hasattr(self, "connect_box"):
            if page_key == "mission":
                self.connect_box.pack(anchor="e", pady=(6, 0))
            else:
                self.connect_box.pack_forget()

        if hasattr(self, "analysis_save_box"):
            if page_key == "analysis":
                self.analysis_save_box.pack(anchor="e", pady=(6, 0))
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

    def _on_provider_selected(self, event=None):
        self.provider_key = self._provider_names.get(self.provider_var.get(), self.provider_key)
        self._save_settings()

    def _switch_language(self, lang_code: str):
        if i18n.get_lang() == lang_code:
            return
        i18n.set_lang(lang_code)
        self._save_settings()

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
            self._redraw_plot()
            self._redraw_takeoff_profile()
            self._redraw_angle_plot()
            self._redraw_landing_plot()
            self._load_trajectory_map()
            self._populate_mission_table(self.analyzer.all_wps)
            self.mission_content.tkraise()

    # ------------------------------------------------------------- обзоры --

    def _pick_date(self):
        """Модальний календар для вибору дати польоту."""
        import datetime
        dlg = tk.Toplevel(self)
        dlg.title("Дата польоту")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()
        dlg.configure(bg=self.palette["bg"])

        # поточна вибрана дата
        try:
            cur = datetime.date.fromisoformat(self.flight_date_var.get())
        except ValueError:
            cur = datetime.date.today()

        state = {"year": cur.year, "month": cur.month}

        header = ttk.Frame(dlg)
        header.pack(fill="x", padx=8, pady=(8, 0))
        month_lbl = ttk.Label(header, font=("Segoe UI", 10, "bold"), width=16, anchor="center")
        month_lbl.pack(side="left", expand=True)

        cal_frame = ttk.Frame(dlg)
        cal_frame.pack(padx=8, pady=4)

        day_btns: list[tk.Button] = []
        selected_cell: dict = {"btn": None}

        DAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]
        for col, dn in enumerate(DAY_NAMES):
            ttk.Label(cal_frame, text=dn, width=4, anchor="center",
                      font=("Segoe UI", 8, "bold")).grid(row=0, column=col, padx=1, pady=2)

        def render(year, month):
            for b in day_btns:
                b.destroy()
            day_btns.clear()

            import calendar
            month_lbl.config(text=f"{calendar.month_name[month]} {year}")
            first_wd, n_days = calendar.monthrange(year, month)
            today = datetime.date.today()

            cell = 0
            for day in range(1, n_days + 1):
                wd = (first_wd + day - 1) % 7
                row = cell // 7 + 1
                col = wd
                d = datetime.date(year, month, day)
                is_sel = (d == cur)
                is_past = (d < today)

                bg = self.palette["blue"] if is_sel else (
                    "#E8ECEF" if is_past else "#DEE3E8"
                )
                fg = self.palette["text_light"] if is_sel else (
                    "#AAAAAA" if is_past else self.palette["text"]
                )

                def pick(date=d):
                    nonlocal cur
                    cur = date
                    self.flight_date_var.set(date.strftime("%Y-%m-%d"))
                    self._save_settings()
                    self._compute_arrival_time()
                    dlg.destroy()

                btn = tk.Button(
                    cal_frame, text=str(day), width=4,
                    bg=bg, fg=fg, relief="flat", bd=0,
                    font=("Segoe UI", 9),
                    activebackground=self.palette["blue"],
                    activeforeground=self.palette["text_light"],
                    cursor="hand2",
                    command=pick,
                    state="disabled" if is_past else "normal",
                )
                btn.grid(row=row, column=col, padx=1, pady=1)
                day_btns.append(btn)
                if wd == 6:
                    cell += 1
                cell += 1

        def prev_month():
            m, y = state["month"] - 1, state["year"]
            if m < 1:
                m, y = 12, y - 1
            state["month"], state["year"] = m, y
            render(y, m)

        def next_month():
            m, y = state["month"] + 1, state["year"]
            if m > 12:
                m, y = 1, y + 1
            state["month"], state["year"] = m, y
            render(y, m)

        ttk.Button(header, text="◀", width=3, command=prev_month).pack(side="left")
        ttk.Button(header, text="▶", width=3, command=next_month).pack(side="right")

        render(state["year"], state["month"])

        # по центру окна
        dlg.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - dlg.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - dlg.winfo_height()) // 3
        dlg.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.wait_window(dlg)

    def _compute_arrival_time(self):
        """Обчислює час прибуття = час вильоту + час польоту (відстань/крейсерська швидкість)."""
        if not hasattr(self, "arrival_time_var"):
            return
        if self.analyzer is None:
            self.arrival_time_var.set("—")
            return
        try:
            speed = float(self.cruise_speed_var.get())
            if speed <= 0:
                raise ValueError
        except ValueError:
            self.arrival_time_var.set("швидкість?")
            return

        total_dist = sum(
            haversine_m(
                self.analyzer.nav_wps[i].lat, self.analyzer.nav_wps[i].lon,
                self.analyzer.nav_wps[i + 1].lat, self.analyzer.nav_wps[i + 1].lon,
            )
            for i in range(len(self.analyzer.nav_wps) - 1)
        )
        flight_seconds = total_dist / speed

        import datetime
        date_str = self.flight_date_var.get().strip()
        time_str = self.flight_time_var.get().strip()
        try:
            departure = datetime.datetime.fromisoformat(f"{date_str}T{time_str}:00")
        except ValueError:
            self.arrival_time_var.set("—")
            return

        arrival = departure + datetime.timedelta(seconds=flight_seconds)
        mins = int(flight_seconds // 60)
        self.arrival_time_var.set(
            f"{arrival.strftime('%H:%M')} (+{mins} хв)"
        )

    def _open_url(self, url: str):
        """Відкриває URL у браузері за замовчуванням."""
        import webbrowser
        if url:
            webbrowser.open(url)

    def _fetch_meteo(self):
        """Запит метеоданих з Open-Meteo для координат старту та посадки."""
        if self.analyzer is None:
            messagebox.showwarning("Метео", "Спочатку завантажте місію")
            return

        date_str = self.flight_date_var.get().strip()
        time_str = self.flight_time_var.get().strip()
        if not date_str:
            messagebox.showwarning("Метео", "Вкажіть дату польоту (наприклад: 2026-08-10)")
            return
        if not time_str:
            messagebox.showwarning("Метео", "Оберіть час вильоту -- без нього неможливо отримати погоду і карта не відмалюється")
            return

        # точка старту -- перша nav-точка, точка посадки -- остання
        wps = self.analyzer.nav_wps
        if not wps:
            messagebox.showwarning("Метео", "Немає точок маршруту")
            return

        start_wp = wps[0]
        land_wp  = wps[-1]

        self._set_meteo_texts("Завантаження метеоданих...", "Завантаження метеоданих...")
        self.notebook.select(0)  # перемикаємось на вкладку «Зліт»
        threading.Thread(
            target=self._meteo_worker,
            args=(date_str, time_str, start_wp, land_wp),
            daemon=True,
        ).start()

    def _meteo_worker(self, date_str, time_str, start_wp, land_wp):
        """Обгортка: ловить БУДЬ-ЯКУ помилку, щоб вона не падала в консоль, а
        показувалась користувачу в обох текстових блоках."""
        try:
            self._meteo_worker_impl(date_str, time_str, start_wp, land_wp)
        except Exception as e:
            err = f"Помилка отримання метео:\n{e}"
            self.after(0, lambda: self._set_meteo_texts(err, err))

    def _meteo_worker_impl(self, date_str, time_str, start_wp, land_wp):
        import urllib.request, json

        hour = 12
        try:
            hour = int(time_str.split(":")[0])
        except Exception:
            pass

        def fetch_point(lat, lon):
            url = (
                f"https://api.open-meteo.com/v1/forecast"
                f"?latitude={lat:.5f}&longitude={lon:.5f}"
                f"&hourly=windspeed_10m,winddirection_10m,temperature_2m"
                f"&daily=sunrise,sunset,windspeed_10m_max,winddirection_10m_dominant"
                f",temperature_2m_max,temperature_2m_min"
                f"&timezone=auto"
                f"&start_date={date_str}&end_date={date_str}"
            )
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "MissionAnalyzer/1.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return json.loads(resp.read()), None
            except Exception as e:
                return None, str(e)

        # азимут полёту: старт→наступна точка і передостання→посадка
        wps = self.analyzer.nav_wps
        az_start = bearing_deg(start_wp.lat, start_wp.lon,
                               wps[1].lat, wps[1].lon) if len(wps) > 1 else 0.0
        az_land  = bearing_deg(wps[-2].lat, wps[-2].lon,
                               land_wp.lat, land_wp.lon) if len(wps) > 1 else 0.0

        map_data = []     # [(wind_dir, wind_spd, flight_az, label, error), ...]
        texts = []         # текст окремо для «Зліт» і «Глісада»

        for wp, label, az in [
            (start_wp, "Старт (Зліт)",   az_start),
            (land_wp,  "Посадка (Глісада)", az_land),
        ]:
            lines = []
            data, err = fetch_point(wp.lat, wp.lon)
            lines.append(f" {label}  ({wp.lat:.5f}, {wp.lon:.5f})")
            lines.append("=" * 44)

            wind_dir, wind_spd = None, None

            if err:
                lines.append(f"  Помилка: {err}")
                map_data.append((None, None, az, label, err))
                texts.append("\n".join(lines))
                continue

            d = data.get("daily", {})
            if d:
                lines.append(f"  Дата            : {date_str}")
                lines.append(f"  Схід сонця      : {(d.get('sunrise') or ['?'])[0]}")
                lines.append(f"  Захід сонця     : {(d.get('sunset')  or ['?'])[0]}")
                t_max = (d.get("temperature_2m_max") or [None])[0]
                t_min = (d.get("temperature_2m_min") or [None])[0]
                lines.append(f"  Темп. (min/max) : {t_min}°C / {t_max}°C")
                ws_max = (d.get("windspeed_10m_max") or [None])[0]
                wd_dom = (d.get("winddirection_10m_dominant") or [None])[0]
                lines.append(f"  Вітер макс.     : {ws_max} км/год, напрямок {wd_dom}°")

            h = data.get("hourly", {})
            times = h.get("time", [])
            target = f"{date_str}T{hour:02d}:00"
            idx = next((i for i, t in enumerate(times) if t == target), None)
            if idx is not None:
                wind_spd = h.get("windspeed_10m",  [None] * (idx + 1))[idx]
                wind_dir = h.get("winddirection_10m", [None] * (idx + 1))[idx]
                tmp      = h.get("temperature_2m", [None] * (idx + 1))[idx]
                lines.append(f"  — На {target} UTC —")
                lines.append(f"  Швидкість вітру : {wind_spd} км/год")
                lines.append(f"  Напрямок вітру  : {wind_dir}°")
                lines.append(f"  Температура     : {tmp}°C")

                if wind_dir is not None and wind_spd is not None:
                    diff = abs((wind_dir - az + 360) % 360)
                    if diff > 180:
                        diff = 360 - diff
                    cross = abs(90 - abs(diff - 90))
                    head_on = diff < 90
                    lines.append(
                        f"  Боковий вітер   : {cross:.0f}°  "
                        f"({'⚠ сильний' if cross > 30 else 'норма'})"
                    )
                    lines.append(
                        f"  Зустрічний вітер: {'так ✓ (добре)' if head_on else 'ні (попутний)'}"
                    )
            else:
                lines.append(f"  Погодинні дані на {target} UTC: недоступні")

            map_data.append((wind_dir, wind_spd, az, label, None))
            texts.append("\n".join(lines))

        self.after(0, lambda: self._on_meteo_ready(texts, map_data))

    def _on_meteo_ready(self, texts: list, map_data: list):
        self._set_meteo_texts(*texts)
        # запускаємо завантаження тайлів для кожної зони в окремих потоках
        for i, (canvas, item) in enumerate(zip(self._meteo_canvases, map_data)):
            wind_dir, wind_spd, flight_az, label, err = item
            if err:
                canvas.delete("all")
                canvas.create_text(
                    canvas.winfo_width() // 2 or 150, canvas.winfo_height() // 2 or 150,
                    text=f"Карта недоступна\n{err}", fill="#FF6666",
                    font=("Segoe UI", 9), justify="center",
                )
                continue
            wp = (self.analyzer.nav_wps[0] if i == 0 else self.analyzer.nav_wps[-1])
            threading.Thread(
                target=self._load_area_tiles,
                args=(canvas, i, wp.lat, wp.lon, flight_az, wind_dir, wind_spd),
                daemon=True,
            ).start()

    def _load_area_tiles(self, canvas: tk.Canvas, idx: int,
                         lat: float, lon: float,
                         flight_az: float | None,
                         wind_dir: float | None, wind_spd: float | None):
        """Фоновий поток: підбираємо зум, завантажуємо тайли 4×4 км, рендеримо."""
        try:
            if self.tile_cache is None:
                # той самий лінивий конструктор, що і в render_map() -- якщо
                # користувач ще жодного разу не відкривав карту на "Місія"
                disk_cache = self.tilecache_var.get().strip() or None
                self.tile_cache = OnlineTileCache(provider=self.provider_key, disk_cache_dir=disk_cache)

            # для локальної карти 4×4 км потрібен ВИСОКИЙ зум (деталізація
            # місцевості), а не self.zoom_var -- той підібраний під ОГЛЯД
            # усього маршруту і може бути навмисно дуже дрібним (мало тайлів
            # на величезну площу), що й давало "півобласті в кадрі"
            zoom = 16
            bounds = None
            for z in range(zoom, 0, -1):
                try:
                    bounds = compute_area_tile_bounds(lat, lon, z)
                    zoom = z
                    break
                except MapTooLargeError:
                    continue
            if bounds is None:
                raise RuntimeError("не вдалось підібрати зум під 400 тайлів")

            tx_min, tx_max, ty_min, ty_max, _ = bounds
            tiles, _ = fetch_tiles(self.tile_cache, tx_min, tx_max, ty_min, ty_max, zoom)
            image_refs = self._meteo_map_images[idx]

            def do_render():
                try:
                    render_area_map(
                        canvas, lat, lon, zoom, tiles, image_refs,
                        tx_min, tx_max, ty_min, ty_max,
                        flight_az=flight_az, wind_dir=wind_dir, wind_spd=wind_spd,
                    )
                    # зберігаємо параметри -- знадобляться для перемальовки,
                    # коли канвас (можливо, прихованої зараз вкладки) реально
                    # отримає свій розмір і викличе <Configure>
                    self._meteo_render_params[idx] = (
                        lat, lon, zoom, tiles, image_refs,
                        tx_min, tx_max, ty_min, ty_max,
                        flight_az, wind_dir, wind_spd,
                    )
                except Exception as e:
                    canvas.delete("all")
                    canvas.create_text(
                        canvas.winfo_width() // 2 or 150, canvas.winfo_height() // 2 or 150,
                        text=f"Помилка відмальовки:\n{e}",
                        fill="#FF6666", font=("Segoe UI", 9), justify="center",
                    )

            self.after(0, do_render)
        except Exception as e:
            err_text = f"Помилка завантаження карти:\n{e}"
            self.after(0, lambda: (
                canvas.delete("all"),
                canvas.create_text(
                    canvas.winfo_width() // 2 or 150, canvas.winfo_height() // 2 or 150,
                    text=err_text, fill="#FF6666", font=("Segoe UI", 9), justify="center",
                ),
            ))

    def _set_meteo_texts(self, start_text: str, land_text: str):
        self._set_text_widget(self.takeoff_weather_text, start_text)
        self._land_weather_text = land_text
        self._refresh_glide_panel()

    def browse_srtm(self):
        path = filedialog.askdirectory(title=i18n.t("dlg_choose_srtm_title"))
        if path:
            self.srtm_var.set(path)
            self._save_settings()

    def browse_tilecache(self):
        path = filedialog.askdirectory(title=i18n.t("dlg_choose_mapcache_title"))
        if path:
            self.tilecache_var.set(path)
            self._save_settings()

    # ------------------------------------------------------------ анализ --

    def _build_analyzer(self, path: str) -> bool:
        """Парсит файл миссии и создаёт self.analyzer (без вызова .analyze()). True при успехе."""
        if not path:
            messagebox.showwarning(i18n.t("msg_no_file_title"), i18n.t("msg_no_file_body"))
            return False
        if not os.path.isfile(path):
            messagebox.showerror(i18n.t("msg_file_not_found_title"), i18n.t("msg_file_not_found_body", path=path))
            return False

        try:
            alt_min = float(self.alt_min_var.get())
            turn_min = float(self.turn_min_var.get())
        except ValueError:
            messagebox.showerror(i18n.t("msg_bad_numbers_title"), i18n.t("msg_bad_numbers_body"))
            return False

        try:
            wps = parse_waypoints(path)
        except Exception as e:
            messagebox.showerror(i18n.t("msg_file_read_error_title"), str(e))
            return False

        terrain = None
        if self.use_srtm_var.get() and self.srtm_var.get().strip():
            try:
                terrain = SRTMTerrain(self.srtm_var.get().strip())
            except SRTMError as e:
                messagebox.showwarning(i18n.t("msg_srtm_unavailable_title"), i18n.t("msg_srtm_unavailable_body", err=e))

        self.analyzer = MissionAnalyzer(wps, alt_min=alt_min, turn_min=turn_min, terrain=terrain)
        self._populate_mission_table(wps)
        return True

    def _populate_mission_table(self, waypoints):
        """Заполняет таблицу миссии (страница «Місія») — як у Mission Planner:
        нульова точка Home (seq=0, команда 16, координати 0/0) не показується."""
        self.mission_table.delete(*self.mission_table.get_children())

        last_pos = None
        for wp in waypoints:
            # пропускаємо нульову Home-точку -- Mission Planner теж її не показує в таблиці
            if wp.index == 0:
                continue

            has_pos = wp.lat != 0 or wp.lon != 0
            dist_str = az_str = ""
            if has_pos and last_pos is not None:
                dist = haversine_m(last_pos[0], last_pos[1], wp.lat, wp.lon)
                az = bearing_deg(last_pos[0], last_pos[1], wp.lat, wp.lon)
                dist_str = f"{dist:.0f}"
                az_str = f"{az:.0f}"
            if has_pos:
                last_pos = (wp.lat, wp.lon)

            self.mission_table.insert("", "end", values=(
                wp.index,
                command_name(wp.command),
                f"{wp.param1:g}", f"{wp.param2:g}", f"{wp.param3:g}", f"{wp.param4:g}",
                f"{wp.lat:.7f}" if has_pos else "",
                f"{wp.lon:.7f}" if has_pos else "",
                f"{wp.alt:g}",
                _frame_name(wp.frame),
                dist_str,
                az_str,
            ))

    def load_mission(self):
        """«Завантажити»: завжди файловий діалог. ArduPilot -- через окрему кнопку Read."""
        self._load_mission_from_file()

    def _load_mission_from_file(self):
        path = filedialog.askopenfilename(
            title=i18n.t("dlg_choose_mission_title"),
            filetypes=[(i18n.t("filetype_waypoints"), "*.waypoints"), (i18n.t("filetype_all"), "*.*")],
        )
        if not path:
            return
        self.file_var.set(path)
        if not self._build_analyzer(path):
            return
        self._finish_load()

    def _load_mission_from_mavlink(self):
        """Запрашивает місію з підключеного польотного контролера (MAVLink MISSION_REQUEST_LIST)."""
        self.status_var.set("Завантаження місії з борту...")
        self.connect_btn.configure(state="disabled")
        threading.Thread(target=self._mavlink_download_worker, daemon=True).start()

    def _mavlink_download_worker(self):
        try:
            from pymavlink import mavutil
            conn = self._flight_conn

            # переконуємось що знаємо цілі (target_system/component).
            # При підключенні ми вже робили wait_heartbeat -- але якщо з'єднання
            # старе і буфер переповнений, скидаємо накопичені повідомлення.
            while conn.recv_match(blocking=False) is not None:
                pass

            # відправляємо MISSION_REQUEST_LIST з повторами
            msg = None
            for attempt in range(3):
                conn.mav.mission_request_list_send(conn.target_system, conn.target_component)
                msg = conn.recv_match(type="MISSION_COUNT", blocking=True, timeout=8)
                if msg is not None:
                    break
            if msg is None:
                raise RuntimeError("Не отримано MISSION_COUNT від борту (3 спроби)")
            count = msg.count

            # завантажуємо всі точки
            import tempfile, os
            items = []
            for i in range(count):
                wp_msg = None
                for attempt in range(3):
                    # спочатку пробуємо INT (ArduPilot >= 3.x підтримує)
                    conn.mav.mission_request_int_send(conn.target_system, conn.target_component, i)
                    wp_msg = conn.recv_match(
                        type=["MISSION_ITEM_INT", "MISSION_ITEM"], blocking=True, timeout=5
                    )
                    if wp_msg is not None:
                        break
                if wp_msg is None:
                    raise RuntimeError(f"Не отримано точку {i}")
                items.append(wp_msg)

            conn.mav.mission_ack_send(conn.target_system, conn.target_component, 0)

            # зберігаємо у тимчасовий .waypoints файл для існуючого парсера
            tmp = tempfile.NamedTemporaryFile(
                mode="w", suffix=".waypoints", delete=False, encoding="utf-8"
            )
            tmp.write("QGC WPL 110\n")
            for item in items:
                msg_type = item.get_type()
                if msg_type == "MISSION_ITEM_INT":
                    lat = item.x / 1e7
                    lon = item.y / 1e7
                    alt = item.z
                else:
                    lat = item.lat
                    lon = item.lon
                    alt = item.alt
                tmp.write(
                    "\t".join(str(v) for v in (
                        item.seq,
                        getattr(item, "current", 0),
                        item.frame,
                        item.command,
                        item.param1, item.param2, item.param3, item.param4,
                        lat, lon, alt,
                        getattr(item, "autocontinue", 1),
                    )) + "\n"
                )
            tmp_path = tmp.name
            tmp.close()

            self.after(0, lambda: self._on_mavlink_mission_ready(tmp_path))
        except Exception as e:
            self.after(0, lambda: self._on_mavlink_error("Завантаження", str(e)))

    def _on_mavlink_mission_ready(self, tmp_path: str):
        import os
        self.connect_btn.configure(state="normal")
        self.file_var.set("ArduPilot (MAVLink)")
        if self._build_analyzer(tmp_path):
            self._finish_load()
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    def _finish_load(self):
        self.mission_content.tkraise()
        self.analyzer.analyze()
        self._distribute_report_text(self._captured_report())
        self._redraw_plot()
        self._redraw_takeoff_profile()
        self._redraw_angle_plot()
        self._redraw_landing_plot()
        self._load_trajectory_map()
        self.status_var.set(
            i18n.t("status_ready_fmt", n=len(self.analyzer.nav_wps), m=len(self.analyzer.issues))
        )
        self._compute_arrival_time()
        self._save_settings()
        self.render_map()

    def _captured_report(self) -> str:
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            self.analyzer.print_report()
        finally:
            sys.stdout = old_stdout
        return buf.getvalue()

    @staticmethod
    def _set_text_widget(widget, text: str):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", text)
        # без власного скролу текст мусить повністю влазити -- підганяємо
        # висоту під реальний вміст (в межах розумного), решту прокручує
        # єдиний спільний повзунок вкладки
        n_lines = max(text.count("\n") + 1, 1)
        widget.configure(height=min(max(n_lines, 3), 40))
        widget.configure(state="disabled")

    def _distribute_report_text(self, report_text: str):
        """
        Розбиває загальний звіт (self.analyzer.print_report()) на секції за
        заголовками "=== ... ===" і розкладає по відповідних вкладках:
        - висота/запас висоти  -> «Траєкторія» (графік висоти)
        - кут траєкторії       -> «Траєкторія» (кут траєкторії)
        - глісада/посадка      -> «Глісада»
        - решта (загальний підсумок) -> до блоку висоти, як загальний огляд
        """
        import re
        parts = re.split(r"(?m)^(=== .* ===)\s*$", report_text)
        # parts[0] -- текст до першого заголовка; далі йдуть пари (заголовок, тіло)

        intro = parts[0].strip()
        elevation_blocks = [intro] if intro else []
        angle_blocks = []
        glide_blocks = []

        i = 1
        while i < len(parts):
            header = parts[i]
            body = parts[i + 1] if i + 1 < len(parts) else ""
            block = (header + "\n" + body).rstrip()
            h_low = header.lower()
            if "гліссад" in h_low or "глісад" in h_low or "посадк" in h_low:
                glide_blocks.append(block)
            elif "кут" in h_low:
                angle_blocks.append(block)
            elif "висот" in h_low:
                elevation_blocks.append(block)
            else:
                # невідома категорія -- краще показати десь, ніж загубити
                elevation_blocks.append(block)
            i += 2

        self._set_text_widget(self.elev_report_text, "\n\n".join(elevation_blocks) or "Без зауважень.")
        self._set_text_widget(self.angle_report_text, "\n\n".join(angle_blocks) or "Без зауважень.")
        self._glide_issues_text = "\n\n".join(glide_blocks) or "Без зауважень по глісаді."
        self._refresh_glide_panel()

    def _refresh_glide_panel(self):
        """Об'єднує звіт по глісаді (зі звіту аналізу) з погодою посадки в один текст."""
        if not hasattr(self, "glide_report_text"):
            return
        weather = self._land_weather_text.strip()
        combined = self._glide_issues_text
        if weather:
            combined += "\n\n" + ("-" * 44) + "\n" + weather
        self._set_text_widget(self.glide_report_text, combined)

    def save_csv(self):
        """«Зберегти»: завжди файловий діалог. ArduPilot -- через окрему кнопку Write."""
        if self.analyzer is None:
            messagebox.showwarning(i18n.t("msg_no_data_title"), i18n.t("msg_no_data_body"))
            return
        self._save_mission_to_file()

    def _save_mission_to_file(self):
        path = filedialog.asksaveasfilename(
            title=i18n.t("dlg_save_csv_title"),
            defaultextension=".csv",
            filetypes=[
                ("CSV", "*.csv"),
                (i18n.t("filetype_waypoints"), "*.waypoints"),
            ],
        )
        if not path:
            return
        if path.lower().endswith(".waypoints"):
            self._export_waypoints(path)
        else:
            self.analyzer.export_csv(path)
        messagebox.showinfo(i18n.t("msg_saved_title"), i18n.t("msg_saved_body", path=path))

    def _save_mission_to_mavlink(self):
        """Записує поточну місію на підключений польотний контролер (MAVLink MISSION_COUNT/ITEM)."""
        self.status_var.set("Запис місії на борт...")
        self.connect_btn.configure(state="disabled")
        threading.Thread(target=self._mavlink_upload_worker, daemon=True).start()

    def _mavlink_upload_worker(self):
        try:
            from pymavlink import mavutil
            conn = self._flight_conn
            wps = self.analyzer.all_wps
            count = len(wps)

            conn.mav.mission_count_send(conn.target_system, conn.target_component, count)

            for _ in range(count):
                req = conn.recv_match(
                    type=["MISSION_REQUEST", "MISSION_REQUEST_INT"],
                    blocking=True, timeout=10,
                )
                if req is None:
                    raise RuntimeError("Борт не запросив наступну точку (timeout)")
                i = req.seq
                wp = wps[i]
                use_int = (req.get_type() == "MISSION_REQUEST_INT")
                if use_int:
                    conn.mav.mission_item_int_send(
                        conn.target_system, conn.target_component,
                        wp.index, wp.frame, wp.command,
                        1 if wp.index == 0 else 0, 1,
                        wp.param1, wp.param2, wp.param3, wp.param4,
                        int(wp.lat * 1e7), int(wp.lon * 1e7), wp.alt,
                        mavutil.mavlink.MAV_MISSION_TYPE_MISSION,
                    )
                else:
                    conn.mav.mission_item_send(
                        conn.target_system, conn.target_component,
                        wp.index, wp.frame, wp.command,
                        1 if wp.index == 0 else 0, 1,
                        wp.param1, wp.param2, wp.param3, wp.param4,
                        wp.lat, wp.lon, wp.alt,
                    )

            ack = conn.recv_match(type="MISSION_ACK", blocking=True, timeout=10)
            if ack is None:
                raise RuntimeError("Не отримано підтвердження від борту (MISSION_ACK timeout)")
            if ack.type != 0:
                raise RuntimeError(f"Борт відхилив місію: код {ack.type}")

            self.after(0, self._on_mavlink_upload_done)
        except Exception as e:
            self.after(0, lambda: self._on_mavlink_error("Запис", str(e)))

    def _on_mavlink_upload_done(self):
        self.connect_btn.configure(state="normal")
        self.status_var.set("Місію записано на борт")
        messagebox.showinfo("MAVLink", "Місію успішно завантажено на борт ArduPilot")

    def _on_mavlink_error(self, action: str, error: str):
        self.connect_btn.configure(state="normal")
        self.status_var.set("")
        messagebox.showerror("MAVLink", f"{action} не вдалося:\n{error}")

    def _export_waypoints(self, path: str):
        """
        Пишет полный список точек (self.analyzer.all_wps) в формате
        QGC WPL 110 -- тот же текстовый .waypoints, который принимает
        Mission Planner и который мы сами читаем при загрузке.
        """
        wps = self.analyzer.all_wps
        with open(path, "w", encoding="utf-8") as f:
            f.write("QGC WPL 110\n")
            for wp in wps:
                current = 1 if wp.index == 0 else 0
                f.write(
                    "\t".join(
                        str(v)
                        for v in (
                            wp.index, current, wp.frame, wp.command,
                            wp.param1, wp.param2, wp.param3, wp.param4,
                            wp.lat, wp.lon, wp.alt, 1,
                        )
                    )
                    + "\n"
                )

    # ------------------------------------------------------------- график --

    def _redraw_plot(self):
        draw_elevation_profile(self.plot_canvas, self.analyzer)

    def _redraw_takeoff_profile(self):
        draw_takeoff_profile(self.takeoff_profile_canvas, self.analyzer, n_wps=3)

    def _redraw_angle_plot(self):
        draw_angle_profile(self.angle_canvas, self.analyzer)

    def _redraw_landing_plot(self):
        draw_landing_approach(self.landing_canvas, self.analyzer)

    # -------------------------------------------------------------- карта --

    def _on_map_wheel(self, event):
        if self.analyzer is None:
            return
        # если сейчас уже идёт загрузка -- игнорируем, чтобы не наплодить потоки
        if self._map_loading:
            return

        # Windows/macOS: event.delta (+120/-120 обычно), Linux: Button-4/Button-5
        if getattr(event, "num", None) == 4:
            direction = 1
        elif getattr(event, "num", None) == 5:
            direction = -1
        else:
            direction = 1 if event.delta > 0 else -1

        new_zoom = max(1, min(19, self.zoom_var.get() + direction))
        if new_zoom == self.zoom_var.get():
            return
        self.zoom_var.set(new_zoom)
        self.render_map()

    def _find_safe_zoom(self, start_zoom: int, min_zoom: int = 1):
        """
        Ищет наибольший зум не выше start_zoom, при котором маршрут
        укладывается в лимит тайлов (см. MapTooLargeError). Чем мельче
        зум, тем крупнее тайлы в реальных метрах и тем меньше их нужно
        для покрытия одной и той же площади -- поэтому идём вниз.
        Возвращает None, если у маршрута вообще нет точек.
        """
        for z in range(start_zoom - 1, min_zoom - 1, -1):
            try:
                compute_tile_bounds(self.analyzer, z)
                return z
            except MapTooLargeError:
                continue
            except ValueError:
                return None
        return min_zoom

    def _load_trajectory_map(self):
        """Завантажує карту всього маршруту для вкладки «Траєкторія» -- та
        сама логіка, що й render_map() на «Місія» (compute_tile_bounds/
        fetch_tiles/render_tiles), тільки малює в окремий канвас."""
        if self.analyzer is None or not hasattr(self, "trajectory_map_canvas"):
            return

        zoom = int(self.zoom_var.get())
        try:
            tx_min, tx_max, ty_min, ty_max, total = compute_tile_bounds(self.analyzer, zoom)
        except MapTooLargeError:
            safe_zoom = self._find_safe_zoom(zoom)
            if safe_zoom is None:
                return
            zoom = safe_zoom
            tx_min, tx_max, ty_min, ty_max, total = compute_tile_bounds(self.analyzer, zoom)
        except ValueError:
            return

        if self.tile_cache is None:
            disk_cache = self.tilecache_var.get().strip() or None
            self.tile_cache = OnlineTileCache(provider=self.provider_key, disk_cache_dir=disk_cache)

        def worker():
            tiles, _cancelled = fetch_tiles(self.tile_cache, tx_min, tx_max, ty_min, ty_max, zoom)
            self.after(0, lambda: self._on_trajectory_map_ready(tiles, zoom, tx_min, tx_max, ty_min, ty_max))

        threading.Thread(target=worker, daemon=True).start()

    def _on_trajectory_map_ready(self, tiles, zoom, tx_min, tx_max, ty_min, ty_max):
        self._trajectory_map_params = (tiles, zoom, tx_min, tx_max, ty_min, ty_max)
        render_tiles(
            self.trajectory_map_canvas, self.analyzer, zoom,
            tx_min, tx_max, ty_min, ty_max, tiles, self._trajectory_map_images,
        )

    def render_map(self):
        if self.analyzer is None:
            messagebox.showwarning(i18n.t("msg_no_data_title"), i18n.t("msg_no_data_body"))
            return

        zoom = int(self.zoom_var.get())
        try:
            tx_min, tx_max, ty_min, ty_max, total = compute_tile_bounds(self.analyzer, zoom)
        except MapTooLargeError:
            # текущий зум слишком мелкий для площади маршрута -- вместо
            # предупреждения молча подбираем самый крупный зум, который
            # ещё укладывается в лимит тайлов, и рисуем картой сразу с ним
            safe_zoom = self._find_safe_zoom(zoom)
            if safe_zoom is None:
                messagebox.showwarning(i18n.t("msg_no_points_title"), i18n.t("msg_no_points_body"))
                return
            zoom = safe_zoom
            self.zoom_var.set(zoom)
            tx_min, tx_max, ty_min, ty_max, total = compute_tile_bounds(self.analyzer, zoom)
        except ValueError:
            messagebox.showwarning(i18n.t("msg_no_points_title"), i18n.t("msg_no_points_body"))
            return

        disk_cache = self.tilecache_var.get().strip() or None
        self.tile_cache = OnlineTileCache(provider=self.provider_key, disk_cache_dir=disk_cache)
        self._save_settings()

        self._cancel_event = threading.Event()
        self._map_loading = True
        self.map_status_var.set(i18n.t("status_loading_tiles_fmt", done=0, total=total))

        def progress_cb(done, tot):
            self.after(0, lambda: self.map_status_var.set(i18n.t("status_loading_tiles_fmt", done=done, total=tot)))

        def worker():
            tiles, cancelled = fetch_tiles(
                self.tile_cache, tx_min, tx_max, ty_min, ty_max, zoom,
                progress_cb=progress_cb, cancel_event=self._cancel_event,
            )
            occupied_polygons = None
            occupied_date = None
            if self.show_occupied_var.get() and not cancelled:
                occ_cache = self.tilecache_var.get().strip() or "map_cache"
                geojson, date_str = fetch_occupied_geojson(occ_cache)
                if geojson is not None:
                    occupied_polygons = extract_polygons(geojson)
                    occupied_date = date_str
            self.after(
                0,
                lambda: self._on_tiles_ready(
                    tiles, zoom, tx_min, tx_max, ty_min, ty_max, cancelled,
                    occupied_polygons, occupied_date,
                ),
            )

        threading.Thread(target=worker, daemon=True).start()

    def _on_tiles_ready(
        self, tiles, zoom, tx_min, tx_max, ty_min, ty_max, cancelled,
        occupied_polygons=None, occupied_date=None,
    ):
        self._map_loading = False

        if cancelled:
            self.map_status_var.set(i18n.t("status_map_cancelled"))
            return

        if self.show_occupied_var.get():
            if occupied_polygons is not None:
                d = f"{occupied_date[:4]}-{occupied_date[4:6]}-{occupied_date[6:]}" if occupied_date else "?"
                self.occupied_status_var.set(i18n.t("occupied_status_date_fmt", date=d))
            else:
                self.occupied_status_var.set(i18n.t("occupied_status_failed"))
        else:
            self.occupied_status_var.set("")

        found, total, missing, undecodable = render_tiles(
            self.map_canvas, self.analyzer, zoom,
            tx_min, tx_max, ty_min, ty_max, tiles, self._map_images,
            overlay_polygons=occupied_polygons,
        )

        status = i18n.t("status_rendered_fmt", found=found, total=total, missing=missing)
        if undecodable:
            status += i18n.t("status_undecodable_suffix_fmt", n=undecodable)
        self.map_status_var.set(status)

        if undecodable and not self._pil_warning_shown:
            self._pil_warning_shown = True
            messagebox.showinfo(i18n.t("msg_need_pillow_title"), i18n.t("msg_need_pillow_body", n=undecodable))


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
