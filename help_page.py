"""
help_page.py — сторінка "Довідка": текст довідки, changelog,
перевірка та застосування оновлень.

HelpPageMixin підмішується до класу App (app.py).
"""

from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

import i18n
import meta
import updater


class HelpPageMixin:
    """Сторінка "Довідка": текст, changelog, оновлення."""

    def _build_help_page(self, content, pad):
        page_help = ttk.Frame(content)
        page_help.grid(row=0, column=0, sticky="nsew")
        self.pages["help"] = page_help

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

        update_row = ttk.Frame(changelog_tab)
        update_row.pack(fill="x", pady=(0, 6))
        self._reg_i18n(
            ttk.Button(update_row, command=self._check_for_updates), "text", "btn_check_updates",
        ).pack(side="left")
        self.update_status_var = tk.StringVar(value="")
        ttk.Label(update_row, textvariable=self.update_status_var, foreground="#666").pack(
            side="left", padx=(10, 0)
        )

        changelog_text = scrolledtext.ScrolledText(changelog_tab, wrap="word", font=("Segoe UI", 10))
        changelog_text.pack(fill="both", expand=True)
        changelog_text.insert("end", f"{i18n.t('app_title')} — {i18n.t('label_version')} {meta.VERSION}")
        changelog_text.insert("end", meta.format_changelog(i18n.get_lang()))
        changelog_text.config(state="disabled")

        # Notebook.tab(text=...) -- інший API, ніж .configure(text=...),
        # тому окремий callback, а не self._reg_i18n. Текст довідки й
        # changelog теж перебудовуємо цілком (звичайний текстовий блок,
        # не окремі віджети з підписами) -- обидва дешеві, без мережі.
        def _retranslate_help_page():
            help_notebook.tab(help_tab, text=i18n.t("tab_help"))
            help_notebook.tab(changelog_tab, text=i18n.t("tab_changelog"))

            help_text.config(state="normal")
            help_text.delete("1.0", "end")
            help_text.insert("end", i18n.t("help_text_body"))
            help_text.config(state="disabled")

            changelog_text.config(state="normal")
            changelog_text.delete("1.0", "end")
            changelog_text.insert("end", f"{i18n.t('app_title')} — {i18n.t('label_version')} {meta.VERSION}")
            changelog_text.insert("end", meta.format_changelog(i18n.get_lang()))
            changelog_text.config(state="disabled")

        self._retranslate_callbacks.append(_retranslate_help_page)

    def _check_for_updates(self, silent: bool = False):
        """Перевіряє GitHub Releases у фоновому потоці. silent=True --
        для тихої перевірки при старті (без повідомлень про помилку/
        відсутність оновлень, лише якщо реально є новіша версія)."""
        if hasattr(self, "update_status_var"):
            self.update_status_var.set(i18n.t("status_checking_updates"))

        def worker():
            try:
                release = updater.check_latest_release()
                has_update = updater.is_newer(release["tag"], meta.VERSION)
            except updater.UpdateError as e:
                self.after(0, lambda: self._on_update_check_done(None, str(e), silent))
                return
            self.after(0, lambda: self._on_update_check_done(release if has_update else False, None, silent))

        threading.Thread(target=worker, daemon=True).start()


    def _on_update_check_done(self, release, error: str | None, silent: bool):
        if error:
            if hasattr(self, "update_status_var"):
                self.update_status_var.set("")
            if not silent:
                messagebox.showerror(i18n.t("msg_update_title"), i18n.t("msg_update_check_failed_body", error=error))
            return

        if release is False:
            if hasattr(self, "update_status_var"):
                self.update_status_var.set(i18n.t("status_up_to_date_fmt", version=meta.VERSION))
            elif not silent:
                messagebox.showinfo(i18n.t("msg_update_title"), i18n.t("msg_latest_version_body", version=meta.VERSION))
            return

        if hasattr(self, "update_status_var"):
            self.update_status_var.set(i18n.t("status_update_available_fmt", tag=release["tag"]))

        body = (release["body"] or "").strip()
        body_preview = (body[:400] + "…") if len(body) > 400 else body
        msg = i18n.t("msg_update_available_body_fmt", tag=release["tag"], current=meta.VERSION)
        if body_preview:
            msg += f"\n\n{i18n.t('msg_update_whats_new')}\n{body_preview}"
        msg += f"\n\n{i18n.t('msg_update_confirm_install')}"

        if messagebox.askyesno(i18n.t("msg_update_available_title"), msg):
            self._apply_update(release)


    def _apply_update(self, release: dict):
        if hasattr(self, "update_status_var"):
            self.update_status_var.set(i18n.t("status_downloading_update_fmt", tag=release["tag"]))

        def worker():
            try:
                app_dir = os.path.dirname(os.path.abspath(__file__))
                backup_dir = updater.download_and_apply_update(release["zip_url"], app_dir)
            except Exception as e:
                self.after(0, lambda: self._on_update_apply_done(None, str(e)))
                return
            self.after(0, lambda: self._on_update_apply_done(backup_dir, None))

        threading.Thread(target=worker, daemon=True).start()


    def _on_update_apply_done(self, backup_dir, error: str | None):
        if error:
            if hasattr(self, "update_status_var"):
                self.update_status_var.set("")
            messagebox.showerror(i18n.t("msg_update_title"), i18n.t("msg_update_install_failed_body", error=error))
            return

        if hasattr(self, "update_status_var"):
            self.update_status_var.set(i18n.t("status_update_installed"))
        messagebox.showinfo(
            i18n.t("msg_update_title"),
            i18n.t("msg_update_installed_body_fmt", backup_dir=backup_dir),
        )


