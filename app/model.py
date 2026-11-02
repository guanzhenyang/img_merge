"""图片条目和编辑状态。编辑在导出前只存在于内存里。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


JPEG_EXTS = frozenset({"jpg", "jpeg", "jpe", "jfif"})
TIFF_EXTS = frozenset({"tif", "tiff"})
SUPPORTED_EXTS = frozenset(
    {"jpg", "jpeg", "jpe", "jfif", "png", "webp", "bmp", "gif", "tif", "tiff"}
)
EXPORT_EXT_CHOICES = ("jpg", "png", "webp", "bmp", "gif", "tiff")

WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
)

MAX_EDGE = 16000
MIN_SCALE = 1.0
MAX_SCALE = 800.0


@dataclass
class EditState:
    stem: str
    ext: str
    rotation: int = 0
    flip_h: bool = False
    flip_v: bool = False
    resize_mode: str = "none"  # none | scale | width | height | exact
    width: int | None = None
    height: int | None = None
    scale: float | None = None
    aspect_lock: bool = True

    def copy(self) -> EditState:
        return EditState(
            stem=self.stem,
            ext=self.ext.lower().lstrip("."),
            rotation=self.rotation % 360,
            flip_h=self.flip_h,
            flip_v=self.flip_v,
            resize_mode=self.resize_mode,
            width=self.width,
            height=self.height,
            scale=self.scale,
            aspect_lock=self.aspect_lock,
        )

    def signature(self) -> tuple:
        scale = None if self.scale is None else round(float(self.scale), 4)
        return (
            self.stem,
            self.ext.lower(),
            self.rotation % 360,
            bool(self.flip_h),
            bool(self.flip_v),
            self.resize_mode,
            self.width,
            self.height,
            scale,
            bool(self.aspect_lock),
        )

    def with_transform_from(self, other: EditState) -> EditState:
        """保留自己的文件名，套用另一份编辑里的旋转、翻转和缩放。"""
        out = self.copy()
        out.rotation = other.rotation % 360
        out.flip_h = other.flip_h
        out.flip_v = other.flip_v
        out.resize_mode = other.resize_mode
        out.width = other.width
        out.height = other.height
        out.scale = other.scale
        out.aspect_lock = other.aspect_lock
        return out


def fresh_edit(path: Path) -> EditState:
    return EditState(stem=path.stem, ext=path.suffix.lower().lstrip("."))


def apply_resolution(edit: EditState, *, mode: str, width: int, height: int | None = None) -> None:
    """批量分辨率。width：统一宽度并按各图比例算高度。stretch：拉成同一宽高。

    不改文件名、旋转和翻转。
    """
    if mode == "width":
        edit.resize_mode = "width"
        edit.width = int(width)
        edit.height = None
        edit.scale = None
        edit.aspect_lock = True
        return
    if mode == "stretch":
        if height is None:
            raise ValueError("拉伸到精确尺寸需要高度")
        edit.resize_mode = "exact"
        edit.width = int(width)
        edit.height = int(height)
        edit.scale = None
        edit.aspect_lock = False
        return
    raise ValueError(f"未知的批量模式：{mode}")


@dataclass
class ImageItem:
    source: Path
    edit: EditState
    original_size: tuple[int, int] | None = None
    file_bytes: int = 0
    read_error: str | None = None

    @property
    def original_ext(self) -> str:
        return self.source.suffix.lower().lstrip(".")


@dataclass
class ExportJob:
    source: Path
    edit: EditState
    original_size: tuple[int, int] | None = None


@dataclass
class ExportReport:
    output_dir: Path
    success: int = 0
    failed: int = 0
    copied: int = 0
    encoded: int = 0
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    written: list[Path] = field(default_factory=list)
    cancelled: bool = False

    @property
    def summary(self) -> str:
        text = f"成功 {self.success} 张，失败 {self.failed} 张"
        if self.success:
            text += f"（直接复制 {self.copied} 张，重新编码 {self.encoded} 张）"
        if self.cancelled:
            text += "，已取消"
        return text


def item_is_modified(item: ImageItem) -> bool:
    edit = item.edit
    renamed = edit.stem != item.source.stem or edit.ext.lower() != item.original_ext
    transformed = bool(edit.rotation % 360 or edit.flip_h or edit.flip_v or edit.resize_mode != "none")
    return renamed or transformed


def describe_transform(edit: EditState) -> str:
    bits: list[str] = []
    if edit.rotation % 360:
        bits.append(f"顺时针旋转 {edit.rotation % 360}°")
    if edit.flip_h:
        bits.append("水平翻转")
    if edit.flip_v:
        bits.append("垂直翻转")
    if edit.resize_mode == "scale":
        bits.append(f"缩放至 {_fmt_scale(edit.scale or 100)}%")
    elif edit.resize_mode == "width" and edit.width:
        lock = "，锁定比例" if edit.aspect_lock else ""
        bits.append(f"宽度 {edit.width}px{lock}")
    elif edit.resize_mode == "height" and edit.height:
        lock = "，锁定比例" if edit.aspect_lock else ""
        bits.append(f"高度 {edit.height}px{lock}")
    elif edit.resize_mode == "exact":
        bits.append(f"拉伸为 {edit.width}×{edit.height}px")
    if not bits:
        return "无旋转、翻转或缩放"
    return "、".join(bits)


def _fmt_scale(value: float) -> str:
    if abs(value - round(value)) < 0.05:
        return str(int(round(value)))
    return f"{value:.1f}"
