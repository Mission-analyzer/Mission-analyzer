"""
config_page.py — сторінка "Конфігурація": параметри аналізу (мін.
висота, мін. кут повороту, крейсерська швидкість), SRTM/тайл-кеш,
провайдер карти, посилання на картографічні/метеосервіси.

ConfigPageMixin підмішується до класу App (app.py).
"""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, ttk

from online_tiles import PROVIDERS
import i18n


class ConfigPageMixin:
    """Сторінка "Конфігурація"."""

    def _build_config_page(self, content, pad):
        page_config = ttk.Frame(content)
        page_config.grid(row=0, column=0, sticky="nsew")
        self.pages["config"] = page_config

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

    def _on_provider_selected(self, event=None):
        self.provider_key = self._provider_names.get(self.provider_var.get(), self.provider_key)
        self._save_settings()


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


