"""启动图片工坊。"""

from __future__ import annotations

import sys
import traceback

import customtkinter as ctk

from app.paths import asset_path, config_dir
from app.ui import WorkbenchApp


def _install_excepthook() -> None:
    def hook(exc_type, value, tb):
        text = "".join(traceback.format_exception(exc_type, value, tb))
        sys.stderr.write(text)
        try:
            with (config_dir() / "error.log").open("a", encoding="utf-8") as handle:
                handle.write(text + "\n")
        except OSError:
            pass

    sys.excepthook = hook


def main() -> None:
    if sys.version_info < (3, 11):
        sys.stderr.write("需要 Python 3.11 或更高版本。\n")
        raise SystemExit(1)
    ctk.set_appearance_mode("dark")
    theme = asset_path("theme.json")
    if theme.exists():
        ctk.set_default_color_theme(str(theme))
    _install_excepthook()
    app = WorkbenchApp()
    app.mainloop()
