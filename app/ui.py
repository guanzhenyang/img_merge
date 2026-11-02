"""图片工坊主窗口：左侧列表和预览，右侧编辑。"""

from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog
from tkinter import font as tkfont

import customtkinter as ctk
from customtkinter.windows.widgets.theme import ThemeManager
from PIL import Image

from app import __version__
from app.dialogs import ask
from app.imaging import (
    collect_images,
    compose_preview,
    export_jobs,
    load_preview_base,
    make_thumbnail,
    oriented_size,
    target_size,
    transform_image,
)
from app.model import (
    EXPORT_EXT_CHOICES,
    JPEG_EXTS,
    MAX_EDGE,
    MAX_SCALE,
    MIN_SCALE,
    EditState,
    ExportJob,
    ExportReport,
    ImageItem,
    describe_transform,
    apply_resolution,
    fresh_edit,
    item_is_modified,
)
from app.paths import (
    asset_path,
    ellipsize,
    format_bytes,
    load_settings,
    name_problem,
    parse_dropped_paths,
    same_container,
    save_settings,
)

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except Exception:  # 没有拖放库时仍可用按钮导入
    DND_FILES = None
    TkinterDnD = None


BG = "#14161C"
CARD = "#232833"
ROW = "#2B3140"
SELECTED = "#3D3424"
LINE = "#3A4150"
TEXT = "#E8EDF5"
MUTED = "#A7B0BE"
ACCENT = "#E0A24A"
OK = "#8DCEA8"
BAD = "#E39B9B"
INK = "#241C0F"

_BASES = (ctk.CTk, TkinterDnD.DnDWrapper) if TkinterDnD is not None else (ctk.CTk,)


def pick_font_family(root: tk.Misc) -> str:
    families = set(tkfont.families(root))
    if sys.platform == "win32":
        candidates = ["Microsoft YaHei UI", "Microsoft YaHei", "SimHei", "PingFang SC"]
    elif sys.platform == "darwin":
        candidates = ["PingFang SC", "Hiragino Sans GB", "Heiti SC", "Songti SC"]
    else:
        candidates = ["WenQuanYi Micro Hei", "Noto Sans CJK SC", "Source Han Sans SC", "Droid Sans Fallback"]
    for name in candidates:
        if name in families:
            return name
    return "TkDefaultFont"


def parse_px(text: str) -> int | None:
    cleaned = text.strip()
    if not cleaned.isdigit():
        return None
    value = int(cleaned)
    if value < 1 or value > MAX_EDGE:
        return None
    return value


def parse_scale(text: str) -> float | None:
    cleaned = text.strip().rstrip("%").strip()
    if not cleaned:
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if value < MIN_SCALE or value > MAX_SCALE:
        return None
    return value


def fmt_scale(value: float) -> str:
    if abs(value - round(value)) < 0.05:
        return str(int(round(value)))
    return f"{value:.1f}"


def square_thumb(image: Image.Image, side: int = 56) -> Image.Image:
    image = image.convert("RGBA")
    image.thumbnail((side, side), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (side, side), (26, 30, 39, 255))
    canvas.paste(image, ((side - image.width) // 2, (side - image.height) // 2), image)
    return canvas


def click_gesture(event) -> str:
    """普通点击替换选中；Shift 选范围；Ctrl（Mac 上还有 Command）加减选中。"""
    state = getattr(event, "state", 0) or 0
    if state & 0x0001:
        return "range"
    if state & 0x0004:
        return "toggle"
    if sys.platform == "darwin" and state & 0x0008:
        return "toggle"
    return "replace"


def open_folder(path: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass


@dataclass
class RowWidgets:
    item: ImageItem
    frame: ctk.CTkFrame
    thumb: ctk.CTkLabel
    name: ctk.CTkLabel
    meta: ctk.CTkLabel
    badge: ctk.CTkLabel
    image: ctk.CTkImage | None = None


class WorkbenchApp(*_BASES):
    def __init__(self):
        ctk.set_appearance_mode("dark")
        theme = asset_path("theme.json")
        if theme.exists():
            ctk.set_default_color_theme(str(theme))
        super().__init__(fg_color=BG)
        self.title("图片工坊")
        self.minsize(1000, 660)
        self._settings = load_settings()
        self._closing = False
        self._dnd_ok = False
        self._drag_inside = 0
        self._exporting = False
        self._cancel_export = False
        self._list_token = 0
        self._thumb_gen = 0
        self._thumbs_inflight = 0
        self._preview_token = 0
        self._preview_job = None
        self._width_job = None
        self._height_job = None
        self._scale_job = None
        self._loading = False
        self.items: list[ImageItem] = []
        self.rows: list[RowWidgets] = []
        self.current_index: int | None = None
        self.selected_ids: set[int] = set()
        self._batch_mode = "stretch"
        self.form: EditState | None = None
        self._preview_base: Image.Image | None = None
        self._preview_base_path: Path | None = None
        self._preview_full_size: tuple[int, int] | None = None
        self._preview_transformed: Image.Image | None = None
        self._preview_ctk: ctk.CTkImage | None = None
        self._preview_box = (0, 0)
        self._edit_widgets: list = []
        self._wrapping: list[ctk.CTkLabel] = []

        family = pick_font_family(self)
        ThemeManager.theme["CTkFont"]["family"] = family
        self.font = ctk.CTkFont(family=family, size=13)
        self.font_small = ctk.CTkFont(family=family, size=12)
        self.font_title = ctk.CTkFont(family=family, size=22, weight="bold")
        self.font_section = ctk.CTkFont(family=family, size=15, weight="bold")

        self._ui_queue: queue.Queue = queue.Queue()
        self._thumb_queue: queue.Queue = queue.Queue()
        self._thumb_thread = threading.Thread(target=self._thumb_loop, name="thumbs", daemon=True)
        self._thumb_thread.start()

        self._build()
        self._setup_dnd()
        self._bind_shortcuts()
        self._set_window_icon()
        self._apply_geometry()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._show_empty_preview()
        self._update_actions()
        self.after(20, self._poll_ui_queue)

    def _on_ui(self, func) -> None:
        """后台线程把界面更新丢进队列，由主线程轮询执行。"""
        self._ui_queue.put(func)

    def _poll_ui_queue(self) -> None:
        if self._closing:
            return
        try:
            while True:
                func = self._ui_queue.get_nowait()
                try:
                    func()
                except Exception:
                    import traceback

                    traceback.print_exc()
        except queue.Empty:
            pass
        try:
            self.after(30, self._poll_ui_queue)
        except Exception:
            pass

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build_header()
        self._build_body()
        self._build_status()

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        header.grid_columnconfigure(1, weight=1)

        logo = asset_path("icon.png")
        if logo.exists():
            image = Image.open(logo).convert("RGBA")
            image.thumbnail((36, 36), Image.Resampling.LANCZOS)
            self._logo = ctk.CTkImage(light_image=image, dark_image=image, size=image.size)
            ctk.CTkLabel(header, image=self._logo, text="").grid(row=0, column=0, rowspan=2, padx=(0, 10))

        ctk.CTkLabel(header, text="图片工坊", font=self.font_title, anchor="w").grid(row=0, column=1, sticky="w")
        ctk.CTkLabel(
            header,
            text="本地预览、重命名、等比缩放与旋转。导出写到新文件夹，原图留在原地。",
            font=self.font_small,
            text_color=MUTED,
            anchor="w",
        ).grid(row=1, column=1, sticky="w")

        actions = ctk.CTkFrame(header, fg_color="transparent")
        actions.grid(row=0, column=2, rowspan=2, sticky="e")
        self.add_files_button = self._button(actions, "添加文件", self.choose_files, kind="primary", width=104)
        self.add_files_button.pack(side="left", padx=(0, 8))
        self.add_folder_button = self._button(actions, "添加文件夹", self.choose_folder, width=116)
        self.add_folder_button.pack(side="left", padx=(0, 8))
        self.clear_button = self._button(actions, "清空列表", self.clear_all, width=104)
        self.clear_button.pack(side="left")

    def _build_body(self) -> None:
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 8))
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=0, minsize=400)
        body.grid_rowconfigure(0, weight=1)

        self.left = ctk.CTkFrame(body, fg_color="transparent")
        self.left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.left.grid_columnconfigure(0, weight=1)
        self.left.grid_rowconfigure(0, weight=2)
        self.left.grid_rowconfigure(1, weight=3)
        self._build_list()
        self._build_preview()

        self.right = ctk.CTkFrame(body, fg_color=CARD, corner_radius=12, border_width=1, border_color=LINE)
        self.right.grid(row=0, column=1, sticky="nsew")
        self.right.grid_columnconfigure(0, weight=1)
        self.right.grid_rowconfigure(0, weight=1)
        self.right.grid_rowconfigure(1, weight=0)
        self.right.bind("<Configure>", self._on_right_configure)
        self._build_editor()

    def _build_list(self) -> None:
        card = ctk.CTkFrame(self.left, fg_color=CARD, corner_radius=12, border_width=1, border_color=LINE)
        card.grid(row=0, column=0, sticky="nsew", pady=(0, 8))
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(2, weight=1)
        self.list_card = card

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        top.grid_columnconfigure(0, weight=1)
        self.list_title = ctk.CTkLabel(top, text="图片列表", font=self.font_section, anchor="w")
        self.list_title.grid(row=0, column=0, sticky="w")
        self.remove_button = self._button(top, "移出当前", self.remove_current, width=96)
        self.remove_button.grid(row=0, column=1, sticky="e")

        self.filter_var = tk.StringVar()
        self.filter_entry = ctk.CTkEntry(
            card,
            textvariable=self.filter_var,
            placeholder_text="筛选文件名",
            height=32,
            font=self.font,
        )
        self.filter_entry.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 6))
        self.filter_var.trace_add("write", lambda *_args: self._apply_filter())

        self.list_scroll = ctk.CTkScrollableFrame(card, fg_color=CARD, corner_radius=0)
        self.list_scroll.grid(row=2, column=0, sticky="nsew", padx=4, pady=(0, 8))
        self._soften_scroll(self.list_scroll)
        self.empty_list = ctk.CTkLabel(
            self.list_scroll,
            text="把图片或文件夹拖到窗口里\n也可以使用上方的添加按钮\n支持 JPG、PNG、WEBP、BMP、GIF、TIFF",
            font=self.font,
            text_color=MUTED,
            justify="center",
        )
        self.empty_list.pack(fill="x", pady=28, padx=12)
        self.filter_empty = ctk.CTkLabel(self.list_scroll, text="没有匹配的文件名", text_color=MUTED, font=self.font)

    def _build_preview(self) -> None:
        card = ctk.CTkFrame(self.left, fg_color=CARD, corner_radius=12, border_width=1, border_color=LINE)
        card.grid(row=1, column=0, sticky="nsew")
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(0, weight=1)
        self.preview_card = card

        self.preview_stage = ctk.CTkFrame(card, fg_color="#12141A", corner_radius=8)
        self.preview_stage.grid(row=0, column=0, sticky="nsew", padx=12, pady=(12, 6))
        self.preview_stage.grid_columnconfigure(0, weight=1)
        self.preview_stage.grid_rowconfigure(0, weight=1)
        self.preview_stage.bind("<Configure>", self._on_preview_configure)

        self.preview_image = ctk.CTkLabel(self.preview_stage, text="")
        self.preview_empty = ctk.CTkLabel(
            self.preview_stage,
            text="",
            font=self.font,
            text_color=MUTED,
            justify="center",
        )
        self.preview_empty.grid(row=0, column=0, sticky="nsew")

        self.caption = ctk.CTkLabel(card, text="", font=self.font_small, text_color=MUTED, anchor="w", justify="left")
        self.caption.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 10))
        self._wrapping.append(self.caption)

    def _build_editor(self) -> None:
        self.edit_scroll = ctk.CTkScrollableFrame(self.right, fg_color=CARD, corner_radius=12)
        self.edit_scroll.grid(row=0, column=0, sticky="nsew", padx=2, pady=2)
        self._soften_scroll(self.edit_scroll)
        parent = self.edit_scroll

        self._section(parent, "文件名")
        name_row = ctk.CTkFrame(parent, fg_color="transparent")
        name_row.pack(fill="x", padx=12)
        self.stem_var = tk.StringVar()
        self.stem_entry = ctk.CTkEntry(name_row, textvariable=self.stem_var, height=34, font=self.font)
        self.stem_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.ext_menu = ctk.CTkOptionMenu(
            name_row,
            values=list(EXPORT_EXT_CHOICES),
            width=88,
            height=34,
            font=self.font,
            command=self._on_ext,
        )
        self.ext_menu.pack(side="left")
        self.stem_var.trace_add("write", self._on_stem)
        self.name_hint = ctk.CTkLabel(parent, text="", font=self.font_small, text_color=BAD, anchor="w", justify="left", wraplength=300)
        self.name_hint.pack(fill="x", padx=12, pady=(4, 0))
        self.format_hint = ctk.CTkLabel(parent, text="", font=self.font_small, text_color=MUTED, anchor="w", justify="left", wraplength=300)
        self.format_hint.pack(fill="x", padx=12, pady=(2, 8))
        self._wrapping.extend([self.name_hint, self.format_hint])

        self._section(parent, "尺寸")
        self.size_label = ctk.CTkLabel(parent, text="选择一张图片后设置导出尺寸", font=self.font_small, text_color=MUTED, anchor="w", justify="left")
        self.size_label.pack(fill="x", padx=12)
        self._wrapping.append(self.size_label)
        dim = ctk.CTkFrame(parent, fg_color="transparent")
        dim.pack(fill="x", padx=12, pady=(6, 0))
        ctk.CTkLabel(dim, text="宽", width=28, font=self.font, anchor="w").pack(side="left")
        self.width_var = tk.StringVar()
        self.width_entry = ctk.CTkEntry(dim, textvariable=self.width_var, width=84, height=34, font=self.font)
        self.width_entry.pack(side="left", padx=(0, 10))
        ctk.CTkLabel(dim, text="高", width=28, font=self.font, anchor="w").pack(side="left")
        self.height_var = tk.StringVar()
        self.height_entry = ctk.CTkEntry(dim, textvariable=self.height_var, width=84, height=34, font=self.font)
        self.height_entry.pack(side="left")
        self.width_entry.bind("<KeyRelease>", lambda _event: self._debounce("_width_job", self._commit_width))
        self.height_entry.bind("<KeyRelease>", lambda _event: self._debounce("_height_job", self._commit_height))
        self.width_entry.bind("<Return>", lambda _event: self._commit_width())
        self.height_entry.bind("<Return>", lambda _event: self._commit_height())
        self.lock_box = ctk.CTkCheckBox(parent, text="锁定宽高比", font=self.font, command=self._on_lock)
        self.lock_box.pack(anchor="w", padx=12, pady=(8, 0))
        self.lock_box.select()
        scale_row = ctk.CTkFrame(parent, fg_color="transparent")
        scale_row.pack(fill="x", padx=12, pady=(8, 4))
        ctk.CTkLabel(scale_row, text="缩放", width=40, font=self.font, anchor="w").pack(side="left")
        self.slider = ctk.CTkSlider(scale_row, from_=1, to=400, number_of_steps=399, command=self._on_slider)
        self.slider.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.slider.set(100)
        self.scale_var = tk.StringVar(value="100")
        self.scale_entry = ctk.CTkEntry(scale_row, textvariable=self.scale_var, width=64, height=34, font=self.font)
        self.scale_entry.pack(side="left")
        ctk.CTkLabel(scale_row, text="%", font=self.font).pack(side="left", padx=(4, 0))
        self.scale_entry.bind("<KeyRelease>", lambda _event: self._debounce("_scale_job", self._commit_scale_entry))
        self.scale_entry.bind("<Return>", lambda _event: self._commit_scale_entry())
        self.lock_hint = ctk.CTkLabel(parent, text="先旋转，再翻转，最后按比例缩放。", font=self.font_small, text_color=MUTED, anchor="w")
        self.lock_hint.pack(fill="x", padx=12, pady=(2, 4))

        self._section(parent, "批量分辨率")
        mode_row = ctk.CTkFrame(parent, fg_color="transparent")
        mode_row.pack(fill="x", padx=12, pady=(2, 4))
        self.batch_stretch_button = self._button(mode_row, "拉伸到精确尺寸", lambda: self._set_batch_mode("stretch"), width=148)
        self.batch_stretch_button.pack(side="left", padx=(0, 6))
        self.batch_width_button = self._button(mode_row, "按宽度等比", lambda: self._set_batch_mode("width"), width=120)
        self.batch_width_button.pack(side="left")
        batch_dim = ctk.CTkFrame(parent, fg_color="transparent")
        batch_dim.pack(fill="x", padx=12, pady=(2, 4))
        ctk.CTkLabel(batch_dim, text="宽", width=28, font=self.font, anchor="w").pack(side="left")
        self.batch_width_var = tk.StringVar(value="900")
        self.batch_width_entry = ctk.CTkEntry(batch_dim, textvariable=self.batch_width_var, width=72, height=34, font=self.font)
        self.batch_width_entry.pack(side="left", padx=(0, 8))
        ctk.CTkLabel(batch_dim, text="高", width=28, font=self.font, anchor="w").pack(side="left")
        self.batch_height_var = tk.StringVar(value="600")
        self.batch_height_entry = ctk.CTkEntry(batch_dim, textvariable=self.batch_height_var, width=72, height=34, font=self.font)
        self.batch_height_entry.pack(side="left")
        batch_actions = ctk.CTkFrame(parent, fg_color="transparent")
        batch_actions.pack(fill="x", padx=12, pady=(2, 2))
        self.batch_selected_button = self._button(batch_actions, "应用到选中", lambda: self.apply_batch_resolution(scope="selected"), width=148)
        self.batch_selected_button.pack(side="left", padx=(0, 6))
        self.batch_all_button = self._button(batch_actions, "应用到全部", lambda: self.apply_batch_resolution(scope="all"), kind="primary", width=120)
        self.batch_all_button.pack(side="left")
        self.batch_hint = ctk.CTkLabel(parent, text="", font=self.font_small, text_color=MUTED, anchor="w", justify="left", wraplength=300)
        self.batch_hint.pack(fill="x", padx=12, pady=(2, 8))
        self._wrapping.append(self.batch_hint)
        self._set_batch_mode("stretch")

        dock = ctk.CTkFrame(self.right, fg_color=CARD, corner_radius=0)
        dock.grid(row=1, column=0, sticky="ew", padx=2, pady=(0, 6))
        ctk.CTkFrame(dock, fg_color=LINE, height=1, corner_radius=0).pack(fill="x", padx=12, pady=(0, 6))
        ctk.CTkLabel(dock, text="旋转与翻转", font=self.font_section, anchor="w").pack(fill="x", padx=12, pady=(0, 4))
        rotate_row = ctk.CTkFrame(dock, fg_color="transparent")
        rotate_row.pack(fill="x", padx=12)
        self.rot_ccw = self._button(rotate_row, "逆时针 90°", lambda: self._rotate(-90), width=108)
        self.rot_ccw.pack(side="left", padx=(0, 6))
        self.rot_cw = self._button(rotate_row, "顺时针 90°", lambda: self._rotate(90), width=108)
        self.rot_cw.pack(side="left", padx=(0, 6))
        self.rot_180 = self._button(rotate_row, "180°", lambda: self._rotate(180), width=72)
        self.rot_180.pack(side="left")
        flip_row = ctk.CTkFrame(dock, fg_color="transparent")
        flip_row.pack(fill="x", padx=12, pady=(6, 0))
        self.flip_h_button = self._button(flip_row, "水平翻转", lambda: self._toggle_flip("h"), width=108)
        self.flip_h_button.pack(side="left", padx=(0, 6))
        self.flip_v_button = self._button(flip_row, "垂直翻转", lambda: self._toggle_flip("v"), width=108)
        self.flip_v_button.pack(side="left")
        self.transform_label = ctk.CTkLabel(dock, text="", font=self.font_small, text_color=MUTED, anchor="w", justify="left", wraplength=300)
        self.transform_label.pack(fill="x", padx=12, pady=(6, 4))
        self._wrapping.append(self.transform_label)
        self.dirty_label = ctk.CTkLabel(dock, text="", font=self.font_small, text_color=ACCENT, anchor="w")
        self.dirty_label.pack(fill="x", padx=12)
        self.apply_button = self._button(dock, "应用到当前图片", self.apply_current, kind="primary", width=340)
        self.apply_button.pack(fill="x", padx=12, pady=(4, 6))
        self.apply_all_button = self._button(dock, "把缩放和旋转应用到全部", lambda: self.apply_to_all(confirm=True), width=340)
        self.apply_all_button.pack(fill="x", padx=12, pady=(0, 6))
        small = ctk.CTkFrame(dock, fg_color="transparent")
        small.pack(fill="x", padx=12, pady=(0, 6))
        self.reset_button = self._button(small, "恢复为原图", self.reset_form, width=148)
        self.reset_button.pack(side="left", padx=(0, 8))
        self.revert_button = self._button(small, "放弃未应用修改", self.revert_form, width=148)
        self.revert_button.pack(side="left")

        out_row = ctk.CTkFrame(dock, fg_color="transparent")
        out_row.pack(fill="x", padx=12)
        initial_out = self._settings.get("output_dir") if isinstance(self._settings.get("output_dir"), str) else ""
        self.output_var = tk.StringVar(value=initial_out or "")
        self.output_entry = ctk.CTkEntry(out_row, textvariable=self.output_var, height=34, font=self.font, placeholder_text="选择输出文件夹")
        self.output_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.output_entry.bind("<FocusOut>", lambda _event: self._save_prefs())
        self.browse_button = self._button(out_row, "浏览", self.choose_output, width=72)
        self.browse_button.pack(side="left")
        self.overwrite_box = ctk.CTkCheckBox(
            dock,
            text="覆盖输出文件夹中的同名文件（不改原图）",
            font=self.font_small,
            command=self._save_prefs,
        )
        self.overwrite_box.pack(anchor="w", padx=12, pady=(8, 0))
        if self._settings.get("overwrite"):
            self.overwrite_box.select()
        export_row = ctk.CTkFrame(dock, fg_color="transparent")
        export_row.pack(fill="x", padx=12, pady=(8, 4))
        self.export_current_button = self._button(export_row, "导出当前", self.export_current, width=148)
        self.export_current_button.pack(side="left", padx=(0, 8))
        self.export_all_button = self._button(export_row, "导出全部", self.export_all, kind="primary", width=148)
        self.export_all_button.pack(side="left")
        self.progress_bar = ctk.CTkProgressBar(dock, height=10)
        self.progress_bar.set(0)
        self.progress_label = ctk.CTkLabel(dock, text="", font=self.font_small, text_color=MUTED, anchor="w")
        self.cancel_button = self._button(dock, "停止导出", self.cancel_export, kind="danger", width=120)
        self.result_label = ctk.CTkLabel(dock, text="还没有导出", font=self.font_small, text_color=MUTED, anchor="w", justify="left", wraplength=300)
        self.result_label.pack(fill="x", padx=12, pady=(8, 8))
        self._wrapping.append(self.result_label)

        self._edit_widgets = [
            self.stem_entry,
            self.ext_menu,
            self.width_entry,
            self.height_entry,
            self.scale_entry,
            self.slider,
            self.lock_box,
            self.rot_ccw,
            self.rot_cw,
            self.rot_180,
            self.flip_h_button,
            self.flip_v_button,
            self.apply_button,
            self.apply_all_button,
            self.batch_stretch_button,
            self.batch_width_button,
            self.batch_width_entry,
            self.batch_height_entry,
            self.batch_selected_button,
            self.batch_all_button,
            self.reset_button,
            self.revert_button,
            self.export_current_button,
            self.export_all_button,
            self.remove_button,
        ]

    def _build_status(self) -> None:
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 10))
        bar.grid_columnconfigure(0, weight=1)
        self.status_label = ctk.CTkLabel(bar, text="准备就绪", font=self.font_small, text_color=MUTED, anchor="w")
        self.status_label.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            bar,
            text=f"v{__version__}   Ctrl+O 添加   Ctrl+Enter 应用   Ctrl+E 导出",
            font=self.font_small,
            text_color=MUTED,
            anchor="e",
        ).grid(row=0, column=1, sticky="e")

    def _section(self, parent, title: str) -> None:
        ctk.CTkLabel(parent, text=title, font=self.font_section, anchor="w").pack(fill="x", padx=12, pady=(8, 2))

    def _button(self, parent, text, command, *, kind="secondary", width=120):
        colors = {}
        if kind == "danger":
            colors = {"fg_color": "#8C4A4A", "hover_color": "#A85C5C", "text_color": "#F8EEEE"}
        elif kind == "secondary":
            colors = {"fg_color": "#2B3140", "hover_color": "#3A4254", "text_color": TEXT}
        return ctk.CTkButton(parent, text=text, command=command, height=34, width=width, font=self.font, **colors)

    def _soften_scroll(self, frame: ctk.CTkScrollableFrame) -> None:
        if sys.platform.startswith("win"):
            return
        try:
            frame._parent_canvas.configure(yscrollincrement=10)
        except Exception:
            pass

    def _on_right_configure(self, event) -> None:
        wrap = min(320, max(200, event.width - 56))
        for label in self._wrapping:
            label.configure(wraplength=wrap)

    # ------------------------------------------------------------------ dnd
    def _setup_dnd(self) -> None:
        if TkinterDnD is None or DND_FILES is None:
            self._set_status("拖放不可用，请使用添加按钮")
            return
        try:
            self.TkdndVersion = TkinterDnD._require(self)
            self.drop_target_register(DND_FILES)
            self.dnd_bind("<<Drop>>", self._on_drop)
            self.dnd_bind("<<DragEnter>>", self._on_drag_enter)
            self.dnd_bind("<<DragLeave>>", self._on_drag_leave)
            self._dnd_ok = True
        except Exception:
            self._set_status("拖放不可用，请使用添加按钮")

    def _on_drag_enter(self, _event):
        self._drag_inside += 1
        self._set_drop_highlight(True)
        return "copy"

    def _on_drag_leave(self, _event):
        self._drag_inside = max(0, self._drag_inside - 1)
        if self._drag_inside == 0:
            self._set_drop_highlight(False)

    def _on_drop(self, event):
        self._drag_inside = 0
        self._set_drop_highlight(False)
        try:
            paths = parse_dropped_paths(event.data, self.tk.splitlist)
        except Exception as exc:
            self._set_status(f"无法识别拖放内容：{exc}")
            return "copy"
        if paths:
            self.import_paths(paths)
        return "copy"

    def _set_drop_highlight(self, active: bool) -> None:
        color = ACCENT if active else LINE
        self.list_card.configure(border_color=color)
        self.preview_card.configure(border_color=color)

    def _bind_shortcuts(self) -> None:
        self.bind_all("<Control-o>", self._shortcut_open)
        self.bind_all("<Control-O>", self._shortcut_open)
        self.bind_all("<Control-Shift-O>", lambda _event: self.choose_folder())
        self.bind_all("<Control-Shift-o>", lambda _event: self.choose_folder())
        self.bind_all("<Control-Return>", lambda _event: self.apply_current())
        self.bind_all("<Control-e>", lambda _event: self.export_all())
        self.bind_all("<Control-E>", lambda _event: self.export_all())
        self.bind_all("<Up>", self._shortcut_nav(-1))
        self.bind_all("<Down>", self._shortcut_nav(1))
        self.bind_all("<Delete>", self._shortcut_delete)
        self.bind_all("<Control-a>", self._shortcut_select_all)
        self.bind_all("<Control-A>", self._shortcut_select_all)
        if sys.platform == "darwin":
            self.bind_all("<Command-a>", self._shortcut_select_all)

    def _shortcut_open(self, _event):
        self.choose_files()
        return "break"

    def _shortcut_nav(self, delta: int):
        def handler(_event):
            if self._typing():
                return None
            self._move_selection(delta)
            return "break"

        return handler

    def _shortcut_delete(self, _event):
        if self._typing():
            return None
        self.remove_current()
        return "break"

    def _shortcut_select_all(self, _event):
        if self._typing() or not self.items:
            return None
        self.selected_ids = {id(item) for item in self.items}
        self._highlight_rows()
        self._update_counts()
        self._set_status(f"已选中全部 {len(self.items)} 张")
        return "break"

    def _typing(self) -> bool:
        widget = self.focus_get()
        if widget is None:
            return False
        return widget.winfo_class() in {"Entry", "Text", "TEntry"}

    def _move_selection(self, delta: int) -> None:
        if not self.items or self.current_index is None:
            return
        target = min(len(self.items) - 1, max(0, self.current_index + delta))
        self.select_index(target)

    # ------------------------------------------------------------------ import
    def choose_files(self) -> None:
        if self._exporting:
            return
        names = filedialog.askopenfilenames(
            parent=self,
            title="选择图片",
            filetypes=[
                ("图片", "*.jpg *.jpeg *.jpe *.jfif *.png *.webp *.bmp *.gif *.tif *.tiff"),
                ("所有文件", "*.*"),
            ],
        )
        if names:
            self.import_paths([Path(name) for name in names])

    def choose_folder(self) -> None:
        if self._exporting:
            return
        name = filedialog.askdirectory(parent=self, title="选择文件夹")
        if name:
            self.import_paths([Path(name)])

    def import_paths(self, paths: list[Path]) -> None:
        if self._exporting:
            self._set_status("正在导出，请稍候再添加")
            return
        cleaned = [Path(path) for path in paths if str(path).strip()]
        if not cleaned:
            return
        self._set_status("正在扫描图片…")
        token = self._list_token

        def work():
            files, notes = collect_images(cleaned)
            self._on_ui(lambda: self._ingest(token, files, notes))

        threading.Thread(target=work, daemon=True).start()

    def import_paths_blocking(self, paths: list[Path]) -> None:
        token = self._list_token
        files, notes = collect_images(list(paths))
        self._ingest(token, files, notes)

    def _ingest(self, token: int, files: list[Path], notes: list[str]) -> None:
        if self._closing or token != self._list_token:
            return
        existing = {self._path_key(item.source) for item in self.items}
        added: list[ImageItem] = []
        duplicates = 0
        for path in files:
            key = self._path_key(path)
            if key in existing:
                duplicates += 1
                continue
            existing.add(key)
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            added.append(ImageItem(source=path, edit=fresh_edit(path), file_bytes=size))
        start = len(self.items)
        self.items.extend(added)
        if self.items and self.empty_list.winfo_manager():
            self.empty_list.pack_forget()
        for index in range(start, len(self.items)):
            self._create_row(index)
            self._request_thumb(index)
        self._apply_filter()
        if self.current_index is None and self.items:
            self.select_index(0, interactive=False)
        self._update_counts()
        self._update_actions()
        if not added and not self.items:
            detail = "\n".join(notes[:12]) if notes else "支持 JPG、PNG、WEBP、BMP、GIF、TIFF。"
            self._set_status("没有找到支持的图片")
            ask(self, "没有导入图片", detail, [("ok", "确定", "primary")])
        else:
            extra = f"，跳过重复 {duplicates} 张" if duplicates else ""
            text = f"已添加 {len(added)} 张{extra}"
            if notes:
                text += "。" + notes[0]
            self._set_status(text)

    def _path_key(self, path: Path) -> str:
        try:
            return str(path.resolve()).lower()
        except OSError:
            return str(path).lower()

    def _create_row(self, index: int) -> None:
        item = self.items[index]
        frame = ctk.CTkFrame(self.list_scroll, fg_color=ROW, corner_radius=8)
        frame.pack(fill="x", padx=6, pady=3)
        frame.grid_columnconfigure(1, weight=1)
        thumb = ctk.CTkLabel(frame, text="", width=56, height=56, fg_color="#1A1E27", corner_radius=6)
        thumb.grid(row=0, column=0, rowspan=2, padx=8, pady=8)
        name = ctk.CTkLabel(frame, text=ellipsize(item.source.name, 32), font=self.font, anchor="w")
        name.grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=(8, 0))
        meta = ctk.CTkLabel(frame, text=format_bytes(item.file_bytes), font=self.font_small, text_color=MUTED, anchor="w")
        meta.grid(row=1, column=1, sticky="ew", padx=(0, 8), pady=(0, 8))
        badge = ctk.CTkLabel(frame, text="", font=self.font_small, text_color=ACCENT, width=36)
        badge.grid(row=0, column=2, rowspan=2, padx=(0, 8))
        row = RowWidgets(item=item, frame=frame, thumb=thumb, name=name, meta=meta, badge=badge)
        self.rows.append(row)
        self._bind_row(row)

    def _bind_row(self, row: RowWidgets) -> None:
        def click(event, item=row.item):
            try:
                index = self.items.index(item)
            except ValueError:
                return
            gesture = click_gesture(event)
            if gesture == "replace":
                self.select_index(index)
            elif gesture == "range":
                self._select_range(index)
            else:
                self._toggle_selected(index)

        def hover(_event, item=row.item):
            self._set_status(str(item.source))

        for widget in (row.frame, row.thumb, row.name, row.meta, row.badge):
            widget.bind("<Button-1>", click)
            widget.bind("<Enter>", hover)

    def _request_thumb(self, index: int) -> None:
        item = self.items[index]
        self._thumbs_inflight += 1
        self._thumb_queue.put((self._thumb_gen, id(item), item.source))

    def _thumb_loop(self) -> None:
        while True:
            task = self._thumb_queue.get()
            if task is None or self._closing:
                return
            gen, item_id, path = task
            if gen != self._thumb_gen:
                continue
            try:
                thumb, size = make_thumbnail(path)
                error = None
            except Exception as exc:
                thumb, size, error = None, None, str(exc)
            self._on_ui(lambda g=gen, i=item_id, t=thumb, s=size, e=error: self._apply_thumb(g, i, t, s, e))

    def _apply_thumb(self, gen: int, item_id: int, thumb, size, error) -> None:
        if gen != self._thumb_gen or self._closing:
            return
        self._thumbs_inflight = max(0, self._thumbs_inflight - 1)
        item = next((candidate for candidate in self.items if id(candidate) == item_id), None)
        if item is None:
            return
        if error:
            item.read_error = error
        if size and item.original_size is None:
            item.original_size = size
            if item is self.current and self._width_job is None and self._height_job is None and self._scale_job is None:
                self._refresh_dimension_entries()
                self._update_summary()
        if thumb is not None:
            thumb = square_thumb(thumb)
            image = ctk.CTkImage(light_image=thumb, dark_image=thumb, size=(56, 56))
            row = self.rows[self.items.index(item)]
            row.image = image
            row.thumb.configure(image=image, text="")
        self._refresh_row(item)

    def _apply_filter(self) -> None:
        query = self.filter_var.get().strip().casefold()
        visible = 0
        for row in self.rows:
            name = f"{row.item.edit.stem}.{row.item.edit.ext}".casefold()
            show = not query or query in name or query in row.item.source.name.casefold()
            if show:
                row.frame.pack(fill="x", padx=6, pady=3)
                visible += 1
            else:
                row.frame.pack_forget()
        if query and self.rows and visible == 0:
            self.filter_empty.pack(fill="x", pady=16)
        elif self.filter_empty.winfo_manager():
            self.filter_empty.pack_forget()

    def _refresh_row(self, item: ImageItem) -> None:
        try:
            row = self.rows[self.items.index(item)]
        except ValueError:
            return
        row.name.configure(text=ellipsize(f"{item.edit.stem}.{item.edit.ext}", 32))
        if item.read_error:
            row.meta.configure(text="无法读取", text_color=BAD)
            row.badge.configure(text="失败", text_color=BAD)
            return
        if item.original_size:
            width, height = item.original_size
            meta = f"{width}×{height} · {format_bytes(item.file_bytes)}"
        else:
            meta = format_bytes(item.file_bytes)
        row.meta.configure(text=meta, text_color=MUTED)
        if item_is_modified(item):
            row.badge.configure(text="已改", text_color=ACCENT)
        else:
            row.badge.configure(text="", text_color=ACCENT)

    def _highlight_rows(self) -> None:
        current = self.current
        chosen = self.selected_ids
        for row in self.rows:
            marked = id(row.item) in chosen or row.item is current
            row.frame.configure(fg_color=SELECTED if marked else ROW)
            row.name.configure(text_color=ACCENT if row.item is current else TEXT)

    def _update_counts(self) -> None:
        modified = sum(1 for item in self.items if item_is_modified(item))
        chosen = sum(1 for item in self.items if id(item) in self.selected_ids)
        if not self.items:
            self.list_title.configure(text="图片列表")
        elif chosen > 1:
            self.list_title.configure(text=f"图片列表  {len(self.items)} 张 · 选中 {chosen} · 已改 {modified}")
        else:
            self.list_title.configure(text=f"图片列表  {len(self.items)} 张 · 已改 {modified}")

    # ------------------------------------------------------------------ select
    @property
    def current(self) -> ImageItem | None:
        if self.current_index is None:
            return None
        if self.current_index < 0 or self.current_index >= len(self.items):
            return None
        return self.items[self.current_index]

    def select_index(self, index: int, *, interactive: bool = True) -> bool:
        if index < 0 or index >= len(self.items):
            return False
        if index == self.current_index:
            self.selected_ids = {id(self.items[index])}
            self._highlight_rows()
            self._update_counts()
            return True
        if interactive and self._is_dirty():
            choice = ask(
                self,
                "尚未应用",
                "这张图片的修改还在预览里。选择不应用后，这些修改会丢掉。",
                [
                    ("apply", "应用并切换", "primary"),
                    ("drop", "不应用", "secondary"),
                    ("cancel", "留在当前", "secondary"),
                ],
            )
            if choice in (None, "cancel"):
                return False
            if choice == "apply" and not self.apply_current():
                return False
        self.current_index = index
        self.selected_ids = {id(self.items[index])}
        self._load_current_form()
        self._highlight_rows()
        self._load_preview()
        self._update_actions()
        self._update_counts()
        return True

    def _toggle_selected(self, index: int) -> None:
        if index < 0 or index >= len(self.items):
            return
        item_id = id(self.items[index])
        if item_id in self.selected_ids and len(self.selected_ids) > 1:
            self.selected_ids.discard(item_id)
        else:
            self.selected_ids.add(item_id)
        if self.current is not None:
            self.selected_ids.add(id(self.current))
        self._highlight_rows()
        self._update_counts()
        self._set_status(f"已选中 {sum(1 for item in self.items if id(item) in self.selected_ids)} 张")

    def _select_range(self, index: int) -> None:
        if index < 0 or index >= len(self.items):
            return
        anchor = self.current_index if self.current_index is not None else index
        start, end = sorted((anchor, index))
        self.selected_ids = {id(self.items[i]) for i in range(start, end + 1)}
        self._highlight_rows()
        self._update_counts()
        self._set_status(f"已选中 {end - start + 1} 张")

    def _load_current_form(self) -> None:
        item = self.current
        if item is None:
            self.form = None
            return
        self.form = item.edit.copy()
        self._fill_entries()
        self._update_summary()
        self._sync_dirty()
        self._update_caption()

    def _fill_entries(self) -> None:
        if self.form is None:
            return
        self._loading = True
        self.stem_var.set(self.form.stem)
        self._set_ext_choices(self.form.ext)
        if self.form.aspect_lock:
            self.lock_box.select()
        else:
            self.lock_box.deselect()
        self._loading = False
        self._refresh_dimension_entries()
        self._style_flips()

    def _set_ext_choices(self, current: str) -> None:
        current = (current or "jpg").lower()
        values = list(EXPORT_EXT_CHOICES)
        if current not in values:
            values.insert(0, current)
        self.ext_menu.configure(values=values)
        self.ext_menu.set(current)

    def _has_size(self) -> bool:
        return bool(self.current and self.current.original_size and self.form)

    def _refresh_dimension_entries(self) -> None:
        if not self._has_size():
            self._loading = True
            self.width_var.set("")
            self.height_var.set("")
            self.scale_var.set("100")
            self.slider.set(100)
            self._loading = False
            return
        assert self.current and self.current.original_size and self.form
        tw, th = target_size(self.current.original_size, self.form)
        ow, _oh = oriented_size(self.current.original_size, self.form.rotation)
        scale = (tw / ow * 100) if ow else 100
        self._loading = True
        self.width_var.set(str(tw))
        self.height_var.set(str(th))
        self.scale_var.set(fmt_scale(scale))
        self.slider.set(min(400, max(1, scale)))
        self._loading = False

    # ------------------------------------------------------------------ form
    def _on_stem(self, *_args) -> None:
        if self._loading or self.form is None:
            return
        self.form.stem = self.stem_var.get()
        self._after_form_change(refit_preview=False)

    def _on_ext(self, value: str) -> None:
        if self._loading or self.form is None:
            return
        self.form.ext = value.lower()
        self._after_form_change(refit_preview=False)

    def _on_lock(self) -> None:
        if self._loading or self.form is None:
            return
        locked = bool(self.lock_box.get())
        self.form.aspect_lock = locked
        if locked and self.form.resize_mode == "exact" and self.form.width:
            self.form.resize_mode = "width"
            self.form.height = None
            self.form.scale = None
        elif not locked and self._has_size() and self.form.resize_mode in {"width", "height", "scale"}:
            assert self.current and self.current.original_size
            tw, th = target_size(self.current.original_size, self.form)
            self.form.resize_mode = "exact"
            self.form.width = tw
            self.form.height = th
            self.form.scale = None
        self._refresh_dimension_entries()
        self._after_form_change(refit_preview=True)

    def _debounce(self, attr: str, func, delay: int = 280) -> None:
        job = getattr(self, attr)
        if job:
            self.after_cancel(job)
        setattr(self, attr, self.after(delay, func))

    def _commit_width(self) -> None:
        self._width_job = None
        if self._loading or not self._has_size() or self.form is None:
            return
        width = parse_px(self.width_var.get())
        if width is None:
            self._set_status(f"宽度需要是 1 到 {MAX_EDGE} 的整数")
            return
        self.form.width = width
        self.form.scale = None
        if self.form.aspect_lock:
            self.form.resize_mode = "width"
            self.form.height = None
        else:
            height = parse_px(self.height_var.get())
            self.form.height = height
            self.form.resize_mode = "exact" if height else "width"
        self._refresh_dimension_entries()
        self._after_form_change(refit_preview=True)

    def _commit_height(self) -> None:
        self._height_job = None
        if self._loading or not self._has_size() or self.form is None:
            return
        height = parse_px(self.height_var.get())
        if height is None:
            self._set_status(f"高度需要是 1 到 {MAX_EDGE} 的整数")
            return
        self.form.height = height
        self.form.scale = None
        if self.form.aspect_lock:
            self.form.resize_mode = "height"
            self.form.width = None
        else:
            width = parse_px(self.width_var.get())
            self.form.width = width
            self.form.resize_mode = "exact" if width else "height"
        self._refresh_dimension_entries()
        self._after_form_change(refit_preview=True)

    def _on_slider(self, value: float) -> None:
        if self._loading or not self._has_size():
            return
        self._apply_scale(float(value), move_slider=False, immediate=False)

    def _commit_scale_entry(self) -> None:
        self._scale_job = None
        if self._loading or not self._has_size():
            return
        parsed = parse_scale(self.scale_var.get())
        if parsed is None:
            self._set_status(f"缩放需要在 {int(MIN_SCALE)} 到 {int(MAX_SCALE)} 之间")
            return
        self._apply_scale(parsed, move_slider=True)

    def _apply_scale(self, scale: float, *, move_slider: bool, immediate: bool = True) -> None:
        if self.form is None:
            return
        scale = min(MAX_SCALE, max(MIN_SCALE, scale))
        if abs(scale - 100) < 0.05:
            self.form.resize_mode = "none"
            self.form.scale = None
        else:
            self.form.resize_mode = "scale"
            self.form.scale = scale
        self.form.width = None
        self.form.height = None
        self._refresh_dimension_entries()
        if move_slider:
            self._loading = True
            self.slider.set(min(400, max(1, scale)))
            self._loading = False
        self._after_form_change(refit_preview=True, immediate=immediate)

    def _rotate(self, delta: int) -> None:
        if self.form is None:
            return
        self.form.rotation = (self.form.rotation + delta) % 360
        self._refresh_dimension_entries()
        self._after_form_change(refit_preview=True)

    def _toggle_flip(self, axis: str) -> None:
        if self.form is None:
            return
        if axis == "h":
            self.form.flip_h = not self.form.flip_h
        else:
            self.form.flip_v = not self.form.flip_v
        self._style_flips()
        self._after_form_change(refit_preview=True)

    def _style_flips(self) -> None:
        self._style_toggle(self.flip_h_button, bool(self.form and self.form.flip_h))
        self._style_toggle(self.flip_v_button, bool(self.form and self.form.flip_v))

    def _style_toggle(self, button, active: bool) -> None:
        if active:
            button.configure(fg_color=ACCENT, hover_color="#F0B56A", text_color=INK)
        else:
            button.configure(fg_color="#2B3140", hover_color="#3A4254", text_color=TEXT)

    def _after_form_change(self, *, refit_preview: bool, immediate: bool = True) -> None:
        self._update_summary()
        self._sync_dirty()
        self._update_caption()
        if refit_preview:
            if immediate:
                self._render_preview()
            else:
                self._schedule_preview()

    def _is_dirty(self) -> bool:
        if not self.current or self.form is None:
            return False
        return self.form.signature() != self.current.edit.signature()

    def _sync_dirty(self) -> None:
        if not self.current:
            self.dirty_label.configure(text="")
            return
        if self._is_dirty():
            self.dirty_label.configure(text="预览已更新，尚未应用到这张图片")
        else:
            self.dirty_label.configure(text="当前修改已应用")

    def _update_summary(self) -> None:
        if not self.current or self.form is None:
            self.size_label.configure(text="选择一张图片后设置导出尺寸")
            self.transform_label.configure(text="")
            self.format_hint.configure(text="")
            self.name_hint.configure(text="")
            return
        item = self.current
        if item.original_size:
            ow, oh = oriented_size(item.original_size, self.form.rotation)
            tw, th = target_size(item.original_size, self.form)
            src = f"{item.original_size[0]}×{item.original_size[1]}"
            if (ow, oh) == item.original_size:
                self.size_label.configure(text=f"原图 {src}    导出 {tw}×{th}")
            else:
                self.size_label.configure(text=f"原图 {src}    旋转后 {ow}×{oh}    导出 {tw}×{th}")
        else:
            self.size_label.configure(text="正在读取尺寸…")
        self.transform_label.configure(text=describe_transform(self.form))
        self.format_hint.configure(text=self._format_hint())
        self.name_hint.configure(text=name_problem(self.form.stem) or "")
        if not self.form.aspect_lock:
            self.lock_hint.configure(text="已关闭比例锁定，宽和高会分别生效。")
        else:
            self.lock_hint.configure(text="先旋转，再翻转，最后按比例缩放。")

    def _format_hint(self) -> str:
        if not self.current or self.form is None:
            return ""
        src = self.current.original_ext
        dst = self.form.ext.lower()
        if same_container(src, dst):
            return "扩展名不变，且没有旋转、翻转或缩放时，直接复制文件，避免再次压缩。"
        if dst in JPEG_EXTS or dst == "bmp":
            return "导出时会转换格式。透明区域会铺成白色。"
        return "导出时会转换成新格式并重新编码。"

    def _update_caption(self) -> None:
        if not self.current or self.form is None:
            self.caption.configure(text="")
            return
        name = f"{self.form.stem}.{self.form.ext}"
        extra = ""
        if self.current.original_size:
            tw, th = target_size(self.current.original_size, self.form)
            extra = f"    导出 {tw}×{th}"
        self.caption.configure(text=f"{name}{extra}\n{self.current.source}")

    def reset_form(self) -> None:
        if not self.current:
            return
        self.form = fresh_edit(self.current.source)
        self._fill_entries()
        self._after_form_change(refit_preview=True)
        self._set_status("已把编辑恢复成原图，点击应用后才会写入列表")

    def revert_form(self) -> None:
        if not self.current:
            return
        self.form = self.current.edit.copy()
        self._fill_entries()
        self._after_form_change(refit_preview=True)
        self._set_status("已放弃尚未应用的修改")

    def apply_current(self) -> bool:
        if not self.current or self.form is None:
            return False
        problem = name_problem(self.form.stem)
        if problem:
            self._set_status(problem)
            self.name_hint.configure(text=problem)
            return False
        if self.form.ext.lower() not in set(EXPORT_EXT_CHOICES) and self.form.ext.lower() != self.current.original_ext:
            # 允许保留原始扩展名，也允许换到导出列表中的格式。
            if self.form.ext.lower() not in {"jpg", "jpeg", "jpe", "jfif", "png", "webp", "bmp", "gif", "tif", "tiff"}:
                self._set_status("请选择支持的扩展名")
                return False
        self.current.edit = self.form.copy()
        self._refresh_row(self.current)
        self._highlight_rows()
        self._update_counts()
        self._sync_dirty()
        self._set_status(f"已应用到 {self.current.edit.stem}.{self.current.edit.ext}")
        return True

    def apply_to_all(self, *, confirm: bool = True) -> bool:
        if not self.items or self.form is None:
            return False
        problem = name_problem(self.form.stem)
        if confirm:
            message = (
                f"把「{describe_transform(self.form)}」应用到全部 {len(self.items)} 张图片。\n"
                "每张图按自己的尺寸计算。文件名保持各自当前的名字。"
            )
            choice = ask(
                self,
                "应用到全部",
                message,
                [("yes", "应用到全部", "primary"), ("no", "取消", "secondary")],
            )
            if choice != "yes":
                return False
        if self.current and not problem:
            self.current.edit.stem = self.form.stem
            self.current.edit.ext = self.form.ext.lower()
        for item in self.items:
            item.edit = item.edit.with_transform_from(self.form)
        if self.current:
            self.form = self.current.edit.copy()
        for item in self.items:
            self._refresh_row(item)
        self._update_counts()
        self._sync_dirty()
        self._set_status(f"已把缩放和旋转应用到全部 {len(self.items)} 张")
        if problem:
            self._set_status(problem + "。缩放和旋转已应用到全部图片。")
        return True

    def _set_batch_mode(self, mode: str) -> None:
        self._batch_mode = "width" if mode == "width" else "stretch"
        self._style_toggle(self.batch_stretch_button, self._batch_mode == "stretch")
        self._style_toggle(self.batch_width_button, self._batch_mode == "width")
        if self._batch_mode == "stretch":
            self.batch_hint.configure(text="拉伸：每张图都变成同样的宽和高，不保留原来的比例。Ctrl 或 Shift 可多选，再点应用到选中。")
        else:
            self.batch_hint.configure(text="按宽度等比：只统一宽度，高度按每张图旋转后的宽高比计算。Ctrl 或 Shift 可多选。")
        self._sync_batch_height_state()

    def _sync_batch_height_state(self) -> None:
        if not hasattr(self, "batch_height_entry"):
            return
        width_state = str(self.batch_width_entry.cget("state"))
        if width_state == "disabled":
            self.batch_height_entry.configure(state="disabled")
            return
        self.batch_height_entry.configure(state="normal" if self._batch_mode == "stretch" else "disabled")

    def _batch_targets(self, scope: str) -> list[ImageItem]:
        if scope == "all":
            return list(self.items)
        chosen = [item for item in self.items if id(item) in self.selected_ids]
        if not chosen and self.current is not None:
            return [self.current]
        return chosen

    def apply_batch_resolution(self, *, scope: str = "all", confirm: bool = True) -> bool:
        """批量设分辨率。stretch 拉成同一宽高；width 只设宽度并按各图比例算高度。"""
        if self._exporting or not self.items:
            self._set_status("请先添加图片")
            return False
        mode = self._batch_mode
        width = parse_px(self.batch_width_var.get())
        if width is None:
            self._set_status(f"批量宽度需要是 1 到 {MAX_EDGE} 的整数")
            return False
        height = None
        if mode == "stretch":
            height = parse_px(self.batch_height_var.get())
            if height is None:
                self._set_status(f"批量高度需要是 1 到 {MAX_EDGE} 的整数")
                return False
        targets = self._batch_targets(scope)
        if not targets:
            self._set_status("没有选中的图片")
            return False
        if confirm and scope == "all":
            if mode == "stretch":
                detail = f"把全部 {len(targets)} 张图片拉伸成 {width}×{height}。比例不同的图会被拉变形。"
            else:
                detail = f"把全部 {len(targets)} 张图片的宽度设为 {width}，高度按每张图自己的比例计算。"
            choice = ask(
                self,
                "批量分辨率",
                detail + "\n文件名、旋转和翻转保持原样。",
                [("yes", "应用到全部", "primary"), ("no", "取消", "secondary")],
            )
            if choice != "yes":
                return False
        for item in targets:
            apply_resolution(item.edit, mode=mode, width=width, height=height)
        if self.form is not None and self.current in targets:
            apply_resolution(self.form, mode=mode, width=width, height=height)
            self._fill_entries()
            self._after_form_change(refit_preview=True)
        for item in targets:
            self._refresh_row(item)
        self._update_counts()
        if mode == "stretch":
            action = f"拉伸为 {width}×{height}"
        else:
            action = f"宽度 {width}（高度按比例）"
        scope_text = "全部" if scope == "all" else "选中"
        self._set_status(f"已将{scope_text} {len(targets)} 张{action}")
        return True

    def remove_current(self) -> None:
        if self.current_index is None or self._exporting:
            return
        index = self.current_index
        removed = self.items.pop(index)
        self.selected_ids.discard(id(removed))
        row = self.rows.pop(index)
        row.frame.destroy()
        self.current_index = None
        self.form = None
        self._preview_base = None
        self._preview_transformed = None
        if self.items:
            self.select_index(min(index, len(self.items) - 1), interactive=False)
        else:
            self._show_empty_preview()
            self.empty_list.pack(fill="x", pady=28, padx=12)
        self._update_counts()
        self._update_actions()
        self._set_status("已从列表移出，原文件仍在磁盘上")

    def clear_all(self) -> None:
        if not self.items or self._exporting:
            return
        modified = sum(1 for item in self.items if item_is_modified(item))
        message = f"从列表移出全部 {len(self.items)} 张图片。磁盘上的原文件会保留。"
        if modified:
            message += f"\n其中 {modified} 张已经有编辑。"
        choice = ask(self, "清空列表", message, [("yes", "清空", "danger"), ("no", "取消", "secondary")])
        if choice != "yes":
            return
        self._clear_items()
        self._set_status("列表已清空")

    def _clear_items(self) -> None:
        self._list_token += 1
        self._thumb_gen += 1
        self._drain_thumbs()
        self._thumbs_inflight = 0
        self._preview_token += 1
        for row in self.rows:
            row.frame.destroy()
        self.rows.clear()
        self.items.clear()
        self.selected_ids.clear()
        self.current_index = None
        self.form = None
        self._preview_base = None
        self._preview_transformed = None
        self._show_empty_preview()
        if not self.empty_list.winfo_manager():
            self.empty_list.pack(fill="x", pady=28, padx=12)
        self._update_counts()
        self._update_actions()

    def _drain_thumbs(self) -> None:
        try:
            while True:
                self._thumb_queue.get_nowait()
        except queue.Empty:
            pass

    # ------------------------------------------------------------------ preview
    def _show_empty_preview(self) -> None:
        self.preview_image.grid_forget()
        self.preview_empty.configure(text="还没有图片\n添加文件或文件夹，或直接拖进窗口")
        self.preview_empty.grid(row=0, column=0, sticky="nsew")
        self.caption.configure(text="")
        self._update_summary()
        self._sync_dirty()

    def _load_preview(self) -> None:
        item = self.current
        self._preview_token += 1
        token = self._preview_token
        self._preview_base = None
        self._preview_transformed = None
        self._preview_base_path = None
        self.preview_image.grid_forget()
        if item is None:
            self._show_empty_preview()
            return
        self.preview_empty.configure(text="正在生成预览…")
        self.preview_empty.grid(row=0, column=0, sticky="nsew")
        path = item.source

        def work():
            try:
                base, full = load_preview_base(path)
                error = None
            except Exception as exc:
                base, full, error = None, None, str(exc)
            self._on_ui(lambda: self._preview_ready(token, path, base, full, error))

        threading.Thread(target=work, daemon=True).start()

    def _preview_ready(self, token: int, path: Path, base, full, error) -> None:
        if self._closing or token != self._preview_token:
            return
        item = self.current
        if item is None or item.source != path:
            return
        if error or base is None:
            item.read_error = error or "无法预览"
            self.preview_empty.configure(text=item.read_error)
            self._refresh_row(item)
            return
        self._preview_base = base
        self._preview_base_path = path
        self._preview_full_size = full
        if item.original_size is None:
            item.original_size = full
            self._refresh_row(item)
            if self._width_job is None and self._height_job is None and self._scale_job is None:
                self._refresh_dimension_entries()
                self._update_summary()
        self._render_preview()

    def _schedule_preview(self) -> None:
        if self._preview_job:
            try:
                self.after_cancel(self._preview_job)
            except Exception:
                pass
        self._preview_job = self.after(40, self._render_preview)

    def _render_preview(self) -> None:
        self._preview_job = None
        if self.current is None or self._preview_base is None or self.form is None:
            return
        if self._preview_base_path != self.current.source:
            return
        self._preview_transformed = transform_image(self._preview_base, self.form, full_size=self._preview_full_size)
        self._fit_preview()

    def _on_preview_configure(self, event) -> None:
        size = (event.width, event.height)
        if abs(size[0] - self._preview_box[0]) < 4 and abs(size[1] - self._preview_box[1]) < 4:
            return
        if event.width < 40 or event.height < 40:
            return
        self._preview_box = size
        self.caption.configure(wraplength=max(200, event.width - 8))
        self._schedule_fit()

    def _schedule_fit(self) -> None:
        if getattr(self, "_fit_job", None):
            try:
                self.after_cancel(self._fit_job)
            except Exception:
                pass
        self._fit_job = self.after(40, self._fit_preview)

    def _fit_preview(self) -> None:
        self._fit_job = None
        if self._preview_transformed is None:
            return
        width = self.preview_stage.winfo_width()
        height = self.preview_stage.winfo_height()
        if width < 40 or height < 40:
            return
        fitted = compose_preview(self._preview_transformed, (width - 20, height - 20))
        image = ctk.CTkImage(light_image=fitted, dark_image=fitted, size=fitted.size)
        self._preview_ctk = image
        self.preview_empty.grid_forget()
        self.preview_image.configure(image=image, text="")
        self.preview_image.grid(row=0, column=0)

    # ------------------------------------------------------------------ export
    def choose_output(self) -> None:
        name = filedialog.askdirectory(parent=self, title="选择输出文件夹")
        if name:
            self.output_var.set(name)
            self._save_prefs()

    def export_current(self) -> None:
        self._export(only_current=True, interactive=True)

    def export_all(self, *, interactive: bool = True):
        return self._export(only_current=False, interactive=interactive)

    def _export(self, *, only_current: bool, interactive: bool):
        if self._exporting:
            return None
        if not self.items or (only_current and self.current is None):
            self._set_status("请先添加图片")
            return None
        include_draft = False
        if self._is_dirty():
            if not interactive:
                include_draft = True
            else:
                choice = ask(
                    self,
                    "有未应用的修改",
                    "当前图片的预览和已应用的编辑不一样。导出时要带上预览里的修改吗？",
                    [
                        ("use", "包含未应用的修改", "primary"),
                        ("skip", "只导出已应用的", "secondary"),
                        ("cancel", "取消", "secondary"),
                    ],
                    width=520,
                )
                if choice in (None, "cancel"):
                    return None
                include_draft = choice == "use"
        output = self._ensure_output(interactive)
        if output is None:
            return None
        jobs = self._jobs(only_current=only_current, include_draft=include_draft)
        overwrite = bool(self.overwrite_box.get())
        if not interactive:
            report = export_jobs(jobs, output, overwrite=overwrite)
            self._remember_report(report)
            return report
        self._exporting = True
        self._cancel_export = False
        self.progress_bar.set(0)
        self.progress_bar.pack(fill="x", padx=12, pady=(8, 0), before=self.result_label)
        self.progress_label.configure(text="正在准备导出…")
        self.progress_label.pack(fill="x", padx=12, pady=(4, 0), before=self.result_label)
        self.cancel_button.pack(anchor="w", padx=12, pady=(6, 0), before=self.result_label)
        self._update_actions()

        def work():
            report = export_jobs(
                jobs,
                output,
                overwrite=overwrite,
                on_progress=self._progress_from_worker,
                should_cancel=lambda: self._cancel_export,
            )
            self._on_ui(lambda: self._export_finished(report))

        threading.Thread(target=work, daemon=True).start()
        return None

    def _jobs(self, *, only_current: bool, include_draft: bool) -> list[ExportJob]:
        chosen = [self.current] if only_current else list(self.items)
        jobs: list[ExportJob] = []
        for item in chosen:
            if item is None:
                continue
            edit = item.edit.copy()
            if include_draft and item is self.current and self.form is not None:
                edit = self.form.copy()
            jobs.append(ExportJob(source=item.source, edit=edit, original_size=item.original_size))
        return jobs

    def _ensure_output(self, interactive: bool) -> Path | None:
        text = self.output_var.get().strip()
        if not text:
            if interactive:
                self.choose_output()
                text = self.output_var.get().strip()
            if not text:
                self._set_status("请选择输出文件夹")
                return None
        output = Path(text).expanduser()
        if output.exists() and not output.is_dir():
            self._set_status("输出位置不是文件夹")
            return None
        if not output.exists():
            if interactive:
                choice = ask(
                    self,
                    "创建文件夹",
                    f"输出文件夹还不存在：\n{output}",
                    [("yes", "创建", "primary"), ("no", "取消", "secondary")],
                )
                if choice != "yes":
                    return None
            try:
                output.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                self._set_status(f"无法创建文件夹：{exc}")
                return None
        return output

    def _progress_from_worker(self, index: int, total: int, name: str) -> None:
        self._on_ui(lambda index=index, total=total, name=name: self._on_export_progress(index, total, name))

    def _on_export_progress(self, index: int, total: int, name: str) -> None:
        if self._closing or total <= 0:
            return
        self.progress_bar.set(max(0, (index - 1) / total))
        self.progress_label.configure(text=f"正在导出 {index}/{total}   {name}")

    def cancel_export(self) -> None:
        if self._exporting:
            self._cancel_export = True
            self._set_status("正在停止导出…")

    def _export_finished(self, report: ExportReport) -> None:
        self._exporting = False
        if self.progress_bar.winfo_manager():
            self.progress_bar.pack_forget()
        if self.progress_label.winfo_manager():
            self.progress_label.pack_forget()
        if self.cancel_button.winfo_manager():
            self.cancel_button.pack_forget()
        self._remember_report(report)
        self._update_actions()
        self._show_report(report)

    def _remember_report(self, report: ExportReport) -> None:
        color = OK if report.failed == 0 and report.success else BAD if report.success == 0 else ACCENT
        self.result_label.configure(text=report.summary, text_color=color)
        self._set_status(report.summary)

    def _show_report(self, report: ExportReport) -> None:
        lines: list[str] = []
        if report.notes:
            lines.append("提示")
            lines.extend(report.notes)
        if report.errors:
            if lines:
                lines.append("")
            lines.append("失败")
            lines.extend(report.errors)
        details = "\n".join(lines) if lines else None
        message = report.summary + f"\n\n位置：{report.output_dir}"
        buttons: list[tuple[str, str, str]]
        if report.written:
            buttons = [("open", "打开输出文件夹", "primary"), ("ok", "确定", "secondary")]
        else:
            buttons = [("ok", "确定", "primary")]
        choice = ask(self, "导出结果", message, buttons, details=details, width=520)
        if choice == "open":
            open_folder(report.output_dir)

    # ------------------------------------------------------------------ chrome
    def _update_actions(self) -> None:
        has_items = bool(self.items)
        has_current = self.current is not None
        editing = has_current and not self._exporting
        self._set_enabled(self._edit_widgets, editing)
        self.clear_button.configure(state="normal" if has_items and not self._exporting else "disabled")
        self.add_files_button.configure(state="normal" if not self._exporting else "disabled")
        self.add_folder_button.configure(state="normal" if not self._exporting else "disabled")
        if has_items and not has_current and not self._exporting:
            self.apply_all_button.configure(state="disabled")
        self.browse_button.configure(state="normal" if not self._exporting else "disabled")
        self.output_entry.configure(state="normal" if not self._exporting else "disabled")
        self.overwrite_box.configure(state="normal" if not self._exporting else "disabled")
        if self._exporting:
            self.cancel_button.configure(state="normal")
        self._sync_batch_height_state()

    def _set_enabled(self, widgets, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for widget in widgets:
            try:
                widget.configure(state=state)
            except Exception:
                pass

    def _set_status(self, text: str) -> None:
        self.status_label.configure(text=text)

    def _set_window_icon(self) -> None:
        png = asset_path("icon.png")
        ico = asset_path("icon.ico")
        try:
            if sys.platform == "win32" and ico.exists():
                self.iconbitmap(default=str(ico))
            if png.exists():
                self._icon_photo = tk.PhotoImage(file=str(png))
                self.iconphoto(True, self._icon_photo)
        except Exception:
            pass

    def _apply_geometry(self) -> None:
        geo = self._settings.get("geometry")
        if isinstance(geo, str) and re.match(r"^\d+x\d+[+-]\d+[+-]\d+$", geo):
            self.geometry(geo)
            return
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        width = min(1320, max(1000, screen_w - 80))
        height = min(980, max(700, screen_h - 80))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _save_prefs(self) -> None:
        if self._closing:
            return
        data = {
            "output_dir": self.output_var.get().strip(),
            "overwrite": bool(self.overwrite_box.get()),
            "geometry": self.geometry(),
        }
        try:
            save_settings(data)
        except OSError:
            pass

    def _on_close(self) -> None:
        if self._exporting:
            choice = ask(self, "正在导出", "导出还在进行。现在关闭会停在已经写完的文件。", [("yes", "关闭", "danger"), ("no", "继续导出", "secondary")])
            if choice != "yes":
                return
            self._cancel_export = True
        if self._is_dirty():
            choice = ask(
                self,
                "尚未应用",
                "当前图片还有未应用的修改。",
                [("apply", "应用并关闭", "primary"), ("drop", "直接关闭", "secondary"), ("cancel", "取消", "secondary")],
            )
            if choice in (None, "cancel"):
                return
            if choice == "apply":
                self.apply_current()
        self._save_prefs()
        self._closing = True
        self._thumb_gen += 1
        try:
            self._thumb_queue.put_nowait(None)
        except Exception:
            pass
        self.destroy()

    def wait_idle(self, timeout: float = 8.0) -> None:
        """测试用：等到缩略图和当前预览就绪。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.update()
            current = self.current
            preview_pending = False
            if current is not None and current.read_error is None and self._preview_base_path != current.source:
                preview_pending = True
            if self._thumbs_inflight == 0 and not preview_pending:
                self.update()
                return
            time.sleep(0.02)
