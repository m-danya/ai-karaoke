from __future__ import annotations

import tkinter as tk
from typing import Callable, Optional


class HoverTooltip:
    def __init__(
        self,
        widget: tk.Widget,
        text: str | Callable[[], str],
        colors: dict[str, str],
        *,
        delay_ms: int = 350,
    ) -> None:
        self.widget = widget
        self.text = text
        self.colors = colors
        self.delay_ms = delay_ms
        self._show_job: Optional[str] = None
        self._window: Optional[tk.Toplevel] = None

        self.widget.bind("<Enter>", self._schedule_show, add="+")
        self.widget.bind("<Leave>", self._hide, add="+")
        self.widget.bind("<ButtonPress>", self._hide, add="+")
        self.widget.bind("<Destroy>", self._handle_destroy, add="+")

    def destroy(self) -> None:
        self._hide()

    def _schedule_show(self, _event=None) -> None:
        self._cancel_show()
        if not self._tooltip_text():
            return
        try:
            self._show_job = self.widget.after(self.delay_ms, self._show)
        except tk.TclError:
            self._show_job = None

    def _cancel_show(self) -> None:
        if self._show_job is None:
            return
        try:
            self.widget.after_cancel(self._show_job)
        except tk.TclError:
            pass
        self._show_job = None

    def _show(self) -> None:
        self._show_job = None
        try:
            if not self.widget.winfo_exists() or not self.widget.winfo_viewable():
                return
        except tk.TclError:
            return
        tooltip_text = self._tooltip_text()
        if not tooltip_text:
            return
        if self._window is not None and self._window.winfo_exists():
            return

        win = tk.Toplevel(self.widget)
        self._window = win
        win.withdraw()
        win.wm_overrideredirect(True)
        try:
            win.attributes("-topmost", True)
        except tk.TclError:
            pass
        win.configure(bg=self.colors["panel_border"])

        label = tk.Label(
            win,
            text=tooltip_text,
            justify="left",
            wraplength=260,
            bg=self.colors["panel"],
            fg=self.colors["text"],
            font=("Fira Sans", 10),
            relief="solid",
            borderwidth=1,
            padx=10,
            pady=6,
        )
        label.pack()

        win.update_idletasks()
        x = self.widget.winfo_rootx() + max(0, (self.widget.winfo_width() - win.winfo_reqwidth()) // 2)
        y = self.widget.winfo_rooty() - win.winfo_reqheight() - 8
        if y < 0:
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        win.geometry(f"+{x}+{y}")
        win.deiconify()

    def _hide(self, _event=None) -> None:
        self._cancel_show()
        if self._window is None:
            return
        try:
            self._window.destroy()
        except tk.TclError:
            pass
        self._window = None

    def _handle_destroy(self, _event=None) -> None:
        self._hide()

    def _tooltip_text(self) -> str:
        value = self.text() if callable(self.text) else self.text
        return str(value).strip()
