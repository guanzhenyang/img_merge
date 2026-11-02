"""中文按钮的简单模态对话框。"""

from __future__ import annotations

import customtkinter as ctk


class ChoiceDialog(ctk.CTkToplevel):
    def __init__(self, master, title: str, message: str, buttons: list[tuple[str, str, str]], details: str | None = None, width: int = 460):
        super().__init__(master)
        self.result: str | None = None
        self._done = False
        self.title(title)
        self.resizable(False, False)
        self.transient(master)
        self.configure(fg_color="#14161C")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=18, pady=16)

        ctk.CTkLabel(
            body,
            text=message,
            justify="left",
            anchor="w",
            wraplength=width - 48,
            font=ctk.CTkFont(size=14),
        ).pack(fill="x", anchor="w")

        if details:
            box = ctk.CTkTextbox(body, height=150, font=ctk.CTkFont(size=12), fg_color="#1A1E27")
            box.pack(fill="both", expand=True, pady=(12, 0))
            box.insert("end", details)
            box.configure(state="disabled")

        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(fill="x", pady=(16, 0))
        self._keys = [key for key, _label, _kind in buttons]
        primary = next((key for key, _label, kind in buttons if kind == "primary"), buttons[-1][0])

        for index, (key, label, kind) in enumerate(buttons):
            button = ctk.CTkButton(
                row,
                text=label,
                height=34,
                width=120,
                font=ctk.CTkFont(size=13),
                command=lambda key=key: self._choose(key),
                **_button_colors(kind),
            )
            button.pack(side="left", padx=(0 if index == 0 else 8, 0))

        self.bind("<Return>", lambda _event: self._choose(primary))
        self.bind("<Escape>", lambda _event: self._escape())
        self.protocol("WM_DELETE_WINDOW", lambda: self._choose(None))
        self.after(20, lambda: self._place(master, width))

    def _place(self, master, width: int) -> None:
        self.update_idletasks()
        height = max(self.winfo_reqheight(), 120)
        try:
            px = master.winfo_rootx()
            py = master.winfo_rooty()
            pw = master.winfo_width()
            ph = master.winfo_height()
        except Exception:
            px, py, pw, ph = 80, 80, width, height
        x = px + max(0, (pw - width) // 2)
        y = py + max(0, (ph - height) // 3)
        self.geometry(f"{width}x{height}+{x}+{y}")
        try:
            self.grab_set()
            self.lift()
            self.focus_force()
        except Exception:
            pass

    def _escape(self) -> None:
        for key in ("cancel", "no", "ok"):
            if key in self._keys:
                self._choose(key)
                return
        self._choose(None)

    def _choose(self, key: str | None) -> None:
        if self._done:
            return
        self._done = True
        self.result = key
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


def _button_colors(kind: str) -> dict:
    if kind == "danger":
        return {"fg_color": "#8C4A4A", "hover_color": "#A85C5C", "text_color": "#F8EEEE"}
    if kind == "secondary":
        return {"fg_color": "#2B3140", "hover_color": "#3A4254", "text_color": "#E8EDF5"}
    return {}


def ask(master, title: str, message: str, buttons: list[tuple[str, str, str]], details: str | None = None, width: int = 460) -> str | None:
    dialog = ChoiceDialog(master, title, message, buttons, details=details, width=width)
    master.wait_window(dialog)
    return dialog.result
