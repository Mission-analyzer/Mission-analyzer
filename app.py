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
from geo import haversine_m, bearing_deg
from srtm import SRTMTerrain, SRTMError
from online_tiles import OnlineTileCache, PROVIDERS
from analyzer import MissionAnalyzer
from elevation_view import draw_elevation_profile
from angle_view import draw_angle_profile
from landing_view import draw_landing_approach
from map_view import compute_tile_bounds, fetch_tiles, render_tiles, bind_pan, MapTooLargeError
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

        lang_box = ttk.Frame(header_inner, style="Header.TFrame")
        lang_box.pack(side="right")
        for lang_code, label in (("uk", "UA"), ("en", "EN")):
            active = i18n.get_lang() == lang_code
            btn = ttk.Button(
                lang_box, text=label, width=4,
                style="LangToggleActive.TButton" if active else "LangToggle.TButton",
                command=lambda lc=lang_code: self._switch_language(lc),
            )
            btn.pack(side="left", padx=2)

        # --- навигационная панель: 4 кнопки (иконка сверху + подпись) ---
        navbar = tk.Frame(self, bg=self.palette["navy_dark"])
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
        ttk.Button(btns, text=i18n.t("btn_load"), command=self.load_mission).pack(side="left")
        ttk.Button(btns, text=i18n.t("btn_save"), command=self.save_csv).pack(side="left", padx=6)

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

        map_ctrl = ttk.Frame(self.mission_content)
        map_ctrl.pack(fill="x", **pad)
        ttk.Label(map_ctrl, text=i18n.t("label_zoom")).pack(side="left", padx=(4, 2))
        ttk.Spinbox(map_ctrl, from_=1, to=19, textvariable=self.zoom_var, width=4).pack(side="left")
        ttk.Label(map_ctrl, text=i18n.t("hint_wheel_zoom"), foreground="#888").pack(side="left", padx=(4, 8))
        self.map_update_btn = ttk.Button(map_ctrl, text=i18n.t("btn_update_map"), command=self.render_map)
        self.map_update_btn.pack(side="left", padx=6)
        self.map_cancel_btn = ttk.Button(
            map_ctrl, text=i18n.t("btn_cancel"), command=self.cancel_map_load, state="disabled"
        )
        self.map_cancel_btn.pack(side="left")
        ttk.Label(map_ctrl, textvariable=self.map_status_var, foreground="#555").pack(side="left", padx=10)

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

        self.notebook = ttk.Notebook(page_analysis)
        self.notebook.pack(fill="both", expand=True, **pad)

        report_tab = ttk.Frame(self.notebook)
        self.notebook.add(report_tab, text=i18n.t("tab_report"))
        self.report = scrolledtext.ScrolledText(report_tab, wrap="word", font=("Consolas", 10))
        self.report.pack(fill="both", expand=True)

        plot_tab = ttk.Frame(self.notebook)
        self.notebook.add(plot_tab, text=i18n.t("tab_elevation"))
        self.plot_canvas = tk.Canvas(plot_tab, bg="white")
        self.plot_canvas.pack(fill="both", expand=True)
        self.plot_canvas.bind("<Configure>", lambda e: self._redraw_plot())

        angle_tab = ttk.Frame(self.notebook)
        self.notebook.add(angle_tab, text=i18n.t("tab_angle"))
        self.angle_canvas = tk.Canvas(angle_tab, bg="white")
        self.angle_canvas.pack(fill="both", expand=True)
        self.angle_canvas.bind("<Configure>", lambda e: self._redraw_angle_plot())

        landing_tab = ttk.Frame(self.notebook)
        self.notebook.add(landing_tab, text=i18n.t("tab_landing"))
        self.landing_canvas = tk.Canvas(landing_tab, bg="white")
        self.landing_canvas.pack(fill="both", expand=True)
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

    def _make_nav_button(self, parent, icon_name: str, text: str, page_key: str) -> tk.Frame:
        """Кнопка навигации: иконка сверху (Canvas, рисуется векторно) + подпись снизу."""
        colors = self.palette
        frame = tk.Frame(parent, bg=colors["navy_dark"], cursor="hand2")
        canvas = tk.Canvas(frame, width=26, height=26, bg=colors["navy_dark"], highlightthickness=0)
        canvas.pack(padx=16, pady=(8, 2))
        label = tk.Label(
            frame, text=text, bg=colors["navy_dark"], fg=colors["text_muted"],
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

        colors = self.palette
        for key, btn in self.nav_buttons.items():
            active = key == page_key
            bg = colors["blue"] if active else colors["navy_dark"]
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
            self.report.insert("end", self._captured_report())
            self.status_var.set(
                i18n.t("status_ready_fmt", n=len(self.analyzer.nav_wps), m=len(self.analyzer.issues))
            )
            self._redraw_plot()
            self._redraw_angle_plot()
            self._redraw_landing_plot()
            self._populate_mission_table(self.analyzer.all_wps)
            self.mission_content.tkraise()

    # ------------------------------------------------------------- обзоры --

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
        """Заполняет таблицу миссии (страница «Місія») — все строки файла, как в самом Mission Planner."""
        self.mission_table.delete(*self.mission_table.get_children())

        last_pos = None  # (lat, lon) последней точки с координатами -- для Відст/AZ
        for wp in waypoints:
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
                wp.frame,
                dist_str,
                az_str,
            ))

    def load_mission(self):
        """«Завантажити»: диалог выбора файла, парсинг, полный анализ, карта — всё за один клик."""
        path = filedialog.askopenfilename(
            title=i18n.t("dlg_choose_mission_title"),
            filetypes=[(i18n.t("filetype_waypoints"), "*.waypoints"), (i18n.t("filetype_all"), "*.*")],
        )
        if not path:
            return
        self.file_var.set(path)
        if not self._build_analyzer(path):
            return

        self.mission_content.tkraise()

        self.analyzer.analyze()
        self.report.delete("1.0", "end")
        self.report.insert("end", self._captured_report())
        self._redraw_plot()
        self._redraw_angle_plot()
        self._redraw_landing_plot()

        self.status_var.set(
            i18n.t("status_ready_fmt", n=len(self.analyzer.nav_wps), m=len(self.analyzer.issues))
        )
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

    def save_csv(self):
        if self.analyzer is None:
            messagebox.showwarning(i18n.t("msg_no_data_title"), i18n.t("msg_no_data_body"))
            return
        path = filedialog.asksaveasfilename(
            title=i18n.t("dlg_save_csv_title"), defaultextension=".csv", filetypes=[("CSV", "*.csv")]
        )
        if not path:
            return
        self.analyzer.export_csv(path)
        messagebox.showinfo(i18n.t("msg_saved_title"), i18n.t("msg_saved_body", path=path))

    # ------------------------------------------------------------- график --

    def _redraw_plot(self):
        draw_elevation_profile(self.plot_canvas, self.analyzer)

    def _redraw_angle_plot(self):
        draw_angle_profile(self.angle_canvas, self.analyzer)

    def _redraw_landing_plot(self):
        draw_landing_approach(self.landing_canvas, self.analyzer)

    # -------------------------------------------------------------- карта --

    def _on_map_wheel(self, event):
        if self.analyzer is None:
            return
        # если сейчас уже идёт загрузка -- игнорируем, чтобы не наплодить потоки
        if str(self.map_update_btn["state"]) == "disabled":
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

    def render_map(self):
        if self.analyzer is None:
            messagebox.showwarning(i18n.t("msg_no_data_title"), i18n.t("msg_no_data_body"))
            return

        zoom = int(self.zoom_var.get())
        try:
            tx_min, tx_max, ty_min, ty_max, total = compute_tile_bounds(self.analyzer, zoom)
        except MapTooLargeError as e:
            messagebox.showwarning(i18n.t("msg_too_large_zoom_title"), i18n.t("msg_too_large_zoom_body", total=e.total))
            return
        except ValueError:
            messagebox.showwarning(i18n.t("msg_no_points_title"), i18n.t("msg_no_points_body"))
            return

        disk_cache = self.tilecache_var.get().strip() or None
        self.tile_cache = OnlineTileCache(provider=self.provider_key, disk_cache_dir=disk_cache)
        self._save_settings()

        self._cancel_event = threading.Event()
        self.map_update_btn.config(state="disabled")
        self.map_cancel_btn.config(state="normal")
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

    def cancel_map_load(self):
        if self._cancel_event is not None:
            self._cancel_event.set()
            self.map_status_var.set(i18n.t("status_cancelling"))

    def _on_tiles_ready(
        self, tiles, zoom, tx_min, tx_max, ty_min, ty_max, cancelled,
        occupied_polygons=None, occupied_date=None,
    ):
        self.map_update_btn.config(state="normal")
        self.map_cancel_btn.config(state="disabled")

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
