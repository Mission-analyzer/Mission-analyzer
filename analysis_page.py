"""
analysis_page.py — усе, що стосується сторінки "Аналіз" в App:
вкладки (Зліт/Маршрут/Посадка), метео, PDF-звіт, графіки (висота, кут,
глісада), карта маршруту зверху.

AnalysisPageMixin підмішується до класу App (app.py).
"""

from __future__ import annotations

import io
import json
import os
import sys
import threading
import urllib.request
import urllib.parse
import datetime
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from analyzer import MissionAnalyzer
from online_tiles import OnlineTileCache
from geo import haversine_m, bearing_deg
from elevation_view import draw_elevation_profile, draw_takeoff_profile
from angle_view import draw_angle_profile
from landing_view import draw_landing_approach
from map_view import compute_tile_bounds, fetch_tiles, render_tiles, bind_pan, MapTooLargeError
from overview_map import compute_area_tile_bounds, render_area_map, render_route_overview
import i18n


class AnalysisPageMixin:
    """Сторінка "Аналіз": вкладки, метео, звіти, графіки, PDF."""

    def _build_analysis_page(self, content, pad):
        page_analysis = ttk.Frame(content)
        page_analysis.grid(row=0, column=0, sticky="nsew")
        self.pages["analysis"] = page_analysis

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

        # плейсхолдер поки не натиснута "Отримати метео" -- вкладки з
        # порожніми/сірими картами й текстом виглядають зламаними, тому
        # ховаємо їх до появи реальних даних
        self.analysis_placeholder = ttk.Frame(page_analysis)
        self.analysis_placeholder.pack(fill="both", expand=True, **pad)
        ttk.Label(
            self.analysis_placeholder,
            text="Натисніть «Отримати метео», щоб побачити аналіз місії",
            font=("Segoe UI", 11),
            foreground="#888",
        ).pack(expand=True)

        self.notebook = ttk.Notebook(page_analysis)
        # не пакуємо одразу -- з'явиться після _fetch_meteo (див. _show_analysis_tabs)

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
                if event.width > 20:
                    outer.itemconfig(inner_id, width=event.width)

            inner.bind("<Configure>", _on_inner_configure)
            outer.bind("<Configure>", _on_outer_configure)

            def _on_wheel(event):
                outer.yview_scroll(int(-1 * (event.delta / 120)), "units")

            # <MouseWheel> НЕ спливає від дочірніх віджетів (текст, канваси
            # карт/графіків) до батьківського контейнера -- тому просте
            # outer.bind()/inner.bind() ловить колесо лише над порожнім
            # місцем. Замість цього тримаємо глобальний перехоплювач, який
            # вмикається/вимикається залежно від того, чи курсор всередині
            # цієї вкладки -- так колесо працює над будь-яким її вмістом.
            def _bind_wheel(_e=None):
                tab.bind_all("<MouseWheel>", _on_wheel)

            def _unbind_wheel(_e=None):
                tab.unbind_all("<MouseWheel>")

            tab.bind("<Enter>", _bind_wheel)
            tab.bind("<Leave>", _unbind_wheel)

            return tab, inner

        def add_map_block(parent, map_title: str, height: int = 460):
            """Карта -- КВАДРАТНА, на всю ширину вкладки. Ширину задає
            fill="x" (надійно працює -- підтверджено скріншотом), а
            висота підганяється під ВЛАСНУ (не чужу) ширину блока напряму
            в його ж <Configure> -- без посередників. Панорамування --
            перетягуванням миші (bind_pan), без окремих смуг прокрутки."""
            map_box = ttk.LabelFrame(parent, text=map_title, height=height)
            map_box.pack(fill="x", pady=(0, 8))
            map_box.pack_propagate(False)

            def _keep_square(event, _box=map_box):
                if event.width > 20 and abs(event.height - event.width) > 2:
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
                height=height, width=1, relief="solid", borderwidth=1,
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
        trajectory_tab, trajectory_inner = make_scroll_tab("Маршрут")

        traj_text_box = ttk.LabelFrame(trajectory_inner, text="Звіт")
        traj_text_box.pack(fill="x", pady=(0, 8))
        ttk.Label(traj_text_box, text=i18n.t("tab_elevation")).pack(anchor="w", padx=4)
        self.elev_report_text = make_plain_text(traj_text_box, height=5)
        self.elev_report_text.pack(fill="x", padx=4, pady=(0, 4))
        ttk.Label(traj_text_box, text=i18n.t("tab_angle")).pack(anchor="w", padx=4)
        self.angle_report_text = make_plain_text(traj_text_box, height=5)
        self.angle_report_text.pack(fill="x", padx=4, pady=(0, 4))

        # карта всього маршруту -- окремий, read-only модуль overview_map.py
        # (без зуму й без можливості редагування -- на відміну від «Місія»,
        # де планується редактор місії; спільна лише "чиста" математика
        # тайлів (compute_tile_bounds/fetch_tiles), сама відмальовка -- ні)
        traj_map_box = ttk.LabelFrame(trajectory_inner, text="Маршрут — вигляд згори", height=460)
        traj_map_box.pack(fill="x", pady=(0, 8))
        traj_map_box.pack_propagate(False)
        self._traj_map_box = traj_map_box

        # НЕ квадрат (на відміну від add_map_block вище/нижче -- ті
        # показують фіксовану площу 4x4 км навколо однієї точки, тому
        # квадрат для них і є правильною формою). Тут -- огляд усього
        # маршруту, а render_route_overview масштабує мозаїку ЛИШЕ по
        # ширині блока (без спроб підігнати ВИСОТУ traj_map_box під
        # пропорції маршруту -- це виявилось ненадійним: висота
        # доступного місця не гумова, і при найменшому розбіжності
        # "contain"-вписування лишало сірі поля по боках). Висота
        # блока лишається фіксованою (460px за замовчуванням). Без
        # власного скролбара -- як і решта карт на "Аналіз" (add_map_block
        # вище/нижче), прокрутка тільки одна, зовнішня, для всієї вкладки.
        self.trajectory_map_canvas = tk.Canvas(traj_map_box, bg="#cccccc", highlightthickness=0, bd=0)
        self.trajectory_map_canvas.pack(fill="both", expand=True)
        bind_pan(self.trajectory_map_canvas)
        self._trajectory_map_params = None  # кеш (tiles, zoom, bounds) -- для перемальовки без повторного фетчу

        def _on_traj_map_configure(event):
            if self._trajectory_map_params is None:
                return
            tiles, zoom, tx_min, tx_max, ty_min, ty_max = self._trajectory_map_params
            img_w, img_h = render_route_overview(
                self.trajectory_map_canvas, self.analyzer, zoom,
                tx_min, tx_max, ty_min, ty_max, tiles, self._trajectory_map_images,
            )
            # traj_map_box росте під реальну висоту карти при поточній
            # ширині -- вкладка "Маршрут" сама прокручується зовні
            # (make_scroll_tab), тому зайва висота тут не проблема, на
            # відміну від "Місія", де немає зовнішньої прокрутки сторінки.
            # верхня межа -- запобіжник від абсурдно високих зображень
            # для маршрутів з екстремальним співвідношенням сторін
            # (величезний діапазон широти при мізерному діапазоні
            # довготи чи навпаки) -- тоді просто лишається трохи більше
            # для прокрутки, ніж поміщається на екран, це прийнятний
            # компроміс порівняно з десятками тисяч пікселів висоти
            traj_map_box.configure(height=min(1600, max(200, img_h)))

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
        landing_tab, landing_inner = make_scroll_tab("Посадка")
        self.glide_report_text = make_plain_text(landing_inner, height=8)
        self.glide_report_text.pack(fill="x", pady=(0, 8))
        add_map_block(landing_inner, "Посадка — 4×4 км")
        landing_chart_box = ttk.LabelFrame(landing_inner, text="Графік глісади")
        landing_chart_box.pack(fill="x", pady=(0, 8))
        self.landing_canvas = tk.Canvas(landing_chart_box, bg="white", height=300)
        self.landing_canvas.pack(fill="x")
        self.landing_canvas.bind("<Configure>", lambda e: self._redraw_landing_plot())


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
            from reportlab.lib.utils import ImageReader
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

        images = self._capture_analysis_images()

        try:
            self._render_analysis_pdf(path, pdfcanvas, A4, mm, pdfmetrics, TTFont, ImageReader, images)
        except Exception as e:
            messagebox.showerror("PDF", f"Не вдалося зберегти PDF:\n{e}")
            return

        messagebox.showinfo("PDF", f"Звіт збережено:\n{path}")


    @staticmethod
    def _grab_widget_image(widget):
        """Знімок поточного вигляду віджета (карти/графіка) для вставки в
        PDF -- через PIL.ImageGrab (потребує, щоб віджет реально був на
        екрані, тобто його вкладка мала бути активною на момент виклику)."""
        try:
            from PIL import ImageGrab
        except ImportError:
            return None
        try:
            widget.update_idletasks()
            x = widget.winfo_rootx()
            y = widget.winfo_rooty()
            w = widget.winfo_width()
            h = widget.winfo_height()
            if w <= 1 or h <= 1:
                return None
            return ImageGrab.grab(bbox=(x, y, x + w, y + h))
        except Exception:
            return None


    def _capture_analysis_images(self) -> dict:
        """Проходить по всіх трьох вкладках «Аналіз», роблячи знімки карт
        і графіків -- ImageGrab бачить лише те, що реально на екрані,
        тому доводиться по черзі перемикати вкладки. Повертає вихідну
        вкладку/видимість плейсхолдера як були."""
        images: dict = {}
        was_visible = self.notebook.winfo_ismapped() if hasattr(self, "notebook") else False
        prev_tab = None
        if was_visible:
            try:
                prev_tab = self.notebook.index(self.notebook.select())
            except tk.TclError:
                prev_tab = None

        self._show_analysis_tabs()

        try:
            self.notebook.select(0)  # Зліт
            self.update()
            if self._meteo_canvases:
                images["takeoff_map"] = self._grab_widget_image(self._meteo_canvases[0])
            images["takeoff_profile"] = self._grab_widget_image(self.takeoff_profile_canvas)

            self.notebook.select(1)  # Маршрут
            self.update()
            images["route_map"] = self._grab_widget_image(self.trajectory_map_canvas)
            images["elevation"] = self._grab_widget_image(self.plot_canvas)
            images["angle"] = self._grab_widget_image(self.angle_canvas)

            self.notebook.select(2)  # Посадка
            self.update()
            if len(self._meteo_canvases) > 1:
                images["landing_map"] = self._grab_widget_image(self._meteo_canvases[1])
            images["landing_profile"] = self._grab_widget_image(self.landing_canvas)
        finally:
            if was_visible and prev_tab is not None:
                self.notebook.select(prev_tab)
            else:
                self._hide_analysis_tabs()

        return images


    def _render_analysis_pdf(self, path, pdfcanvas, A4, mm, pdfmetrics, TTFont, ImageReader, images: dict):
        """Формує PDF зі звітом: Зліт (погода+карта+профіль), Маршрут
        (звіти+карта маршруту+графіки висоти/кута), Посадка
        (проблеми+погода+карта+графік глісади). Карти/графіки вставляються
        як знімки екрана (PIL.ImageGrab), зроблені перед викликом цього
        методу -- сам pdfcanvas не має доступу до вікна програми."""
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

        def write_image(pil_img, max_w_mm=170, max_h_mm=110):
            nonlocal y
            if pil_img is None:
                return
            iw, ih = pil_img.size
            if iw <= 1 or ih <= 1:
                return
            max_w = max_w_mm * mm
            max_h = max_h_mm * mm
            scale = min(max_w / iw, max_h / ih, 1.0)
            draw_w = iw * scale
            draw_h = ih * scale
            if y - draw_h < margin:
                new_page()
            c.drawImage(
                ImageReader(pil_img), margin, y - draw_h,
                width=draw_w, height=draw_h,
                preserveAspectRatio=True, mask="auto",
            )
            y -= draw_h + line_h

        c.setFont(font_name, 9)
        write_title("Звіт аналізу місії — Mission Analyzer")
        write_body(f"Файл місії: {self.file_var.get() or '—'}")
        write_body(f"Дата польоту: {self.flight_date_var.get() or '—'}   "
                   f"Час вильоту (UTC): {self.flight_time_var.get() or '—'}   "
                   f"Прибуття: {self.arrival_time_var.get() if hasattr(self, 'arrival_time_var') else '—'}")
        y -= line_h

        write_heading("Зліт — погода в точці старту")
        write_body(self._get_text(self.takeoff_weather_text) or "Немає даних (натисніть «Отримати метео»).")
        write_image(images.get("takeoff_map"))
        write_image(images.get("takeoff_profile"))
        y -= line_h

        write_heading("Маршрут — карта")
        write_image(images.get("route_map"))

        write_heading("Маршрут — графік висоти")
        write_body(self._get_text(self.elev_report_text) or "Без зауважень.")
        write_image(images.get("elevation"))
        y -= line_h

        write_heading("Маршрут — кут траєкторії")
        write_body(self._get_text(self.angle_report_text) or "Без зауважень.")
        write_image(images.get("angle"))
        y -= line_h

        write_heading("Посадка — проблеми та погода посадки")
        write_body(self._get_text(self.glide_report_text) or "Без зауважень.")
        write_image(images.get("landing_map"))
        write_image(images.get("landing_profile"))

        c.save()


    @staticmethod
    def _get_text(widget) -> str:
        return widget.get("1.0", "end").rstrip()


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


    def _hide_analysis_tabs(self):
        """Повертає плейсхолдер замість вкладок -- викликається при
        завантаженні нової місії, доки для неї ще не отримано погоду."""
        if hasattr(self, "notebook"):
            self.notebook.pack_forget()
        if hasattr(self, "analysis_placeholder"):
            pad = {"padx": 6, "pady": 4}
            self.analysis_placeholder.pack(fill="both", expand=True, **pad)


    def _show_analysis_tabs(self):
        """Ховає плейсхолдер і показує вкладки «Аналіз» -- викликається,
        коли користувач натискає «Отримати метео» (до того порожні/сірі
        вкладки виглядали б зламаними)."""
        self._ensure_analysis_built()
        if hasattr(self, "analysis_placeholder"):
            self.analysis_placeholder.pack_forget()
        pad = {"padx": 6, "pady": 4}
        self.notebook.pack(fill="both", expand=True, **pad)


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

        self._show_analysis_tabs()
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
            (start_wp, "Старт (Зліт)", az_start),
            (land_wp,  "Посадка",      az_land),
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


    def _ensure_analysis_built(self):
        """Лінива побудова важких елементів «Аналіз» (графіки, карта
        маршруту) -- рахуються один раз, при першому реальному показі
        вкладок, а не при кожному завантаженні місії на «Місія»."""
        if getattr(self, "_analysis_built", False) or self.analyzer is None:
            return
        self._redraw_plot()
        self._redraw_takeoff_profile()
        self._redraw_angle_plot()
        self._redraw_landing_plot()
        self._load_trajectory_map()
        self._analysis_built = True


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
        elevation_blocks = []
        angle_blocks = []
        glide_blocks = []

        if intro:
            # у вступному тексті (до першого "=== ... ===") може ховатися
            # абзац "Глісада заходу на посадку..." без власного заголовка --
            # витягуємо його окремо, решта інтро йде в блок висоти
            intro_paragraphs = re.split(r"\n\s*\n", intro)
            intro_elevation_parts = []
            for para in intro_paragraphs:
                if "глісад" in para.lower():
                    glide_blocks.append(para.strip())
                else:
                    intro_elevation_parts.append(para)
            if intro_elevation_parts:
                elevation_blocks.append("\n\n".join(intro_elevation_parts))

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


    def _redraw_plot(self):
        draw_elevation_profile(self.plot_canvas, self.analyzer)


    def _redraw_takeoff_profile(self):
        draw_takeoff_profile(self.takeoff_profile_canvas, self.analyzer, n_wps=3)


    def _redraw_angle_plot(self):
        draw_angle_profile(self.angle_canvas, self.analyzer)


    def _redraw_landing_plot(self):
        draw_landing_approach(self.landing_canvas, self.analyzer)

    # -------------------------------------------------------------- карта --


    def _load_trajectory_map(self):
        """Завантажує карту всього маршруту для вкладки «Траєкторія».

        Зум -- саме той, що автоматично порахувався один раз при
        завантаженні місії на "Місія" (self._initial_auto_zoom,
        встановлюється в render_map(auto_zoom=True) в mission_page.py),
        а не self.zoom_var (те можна покрутити вручну колесом миші на
        "Місія" вже ПІСЛЯ завантаження -- "Маршрут" на це реагувати не
        повинен) і не окремий підбір під власний канвас (просто зайва
        складність без потреби).
        """
        if self.analyzer is None or not hasattr(self, "trajectory_map_canvas"):
            return

        zoom = getattr(self, "_initial_auto_zoom", None)
        if zoom is None:
            zoom = int(self.zoom_var.get())
        tx_min, tx_max, ty_min, ty_max, total = compute_tile_bounds(self.analyzer, zoom)

        if self.tile_cache is None:
            disk_cache = self.tilecache_var.get().strip() or None
            self.tile_cache = OnlineTileCache(provider=self.provider_key, disk_cache_dir=disk_cache)

        def worker():
            tiles, _cancelled = fetch_tiles(self.tile_cache, tx_min, tx_max, ty_min, ty_max, zoom)
            self.after(0, lambda: self._on_trajectory_map_ready(tiles, zoom, tx_min, tx_max, ty_min, ty_max))

        threading.Thread(target=worker, daemon=True).start()


    def _on_trajectory_map_ready(self, tiles, zoom, tx_min, tx_max, ty_min, ty_max):
        self._trajectory_map_params = (tiles, zoom, tx_min, tx_max, ty_min, ty_max)
        img_w, img_h = render_route_overview(
            self.trajectory_map_canvas, self.analyzer, zoom,
            tx_min, tx_max, ty_min, ty_max, tiles, self._trajectory_map_images,
        )
        # верхня межа -- запобіжник від абсурдно високих зображень
        # для маршрутів з екстремальним співвідношенням сторін
        # (величезний діапазон широти при мізерному діапазоні
        # довготи чи навпаки) -- тоді просто лишається трохи більше
        # для прокрутки, ніж поміщається на екран, це прийнятний
        # компроміс порівняно з десятками тисяч пікселів висоти
        self._traj_map_box.configure(height=min(1600, max(200, img_h)))


