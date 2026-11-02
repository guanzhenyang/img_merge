"""扫描、变换和导出。几何顺序：校正 EXIF 方向，顺时针旋转，翻转，最后缩放。"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from app.model import (
    JPEG_EXTS,
    SUPPORTED_EXTS,
    TIFF_EXTS,
    EditState,
    ExportJob,
    ExportReport,
)
from app.paths import name_problem, same_container, sanitize_stem

Image.MAX_IMAGE_PIXELS = 250_000_000

SKIP_DIRS = {".git", "__MACOSX", ".thumbnails", "@eaDir"}
JPEG_QUALITY = 90
WEBP_QUALITY = 90


class ImageReadError(Exception):
    pass


def _is_supported(path: Path) -> bool:
    return path.is_file() and path.suffix.lower().lstrip(".") in SUPPORTED_EXTS


def collect_images(paths: list[Path]) -> tuple[list[Path], list[str]]:
    """递归收集图片。文件夹里的非图片只记数量，单独点选的不支持文件会写出文件名。"""
    found: list[Path] = []
    notes: list[str] = []
    seen: set[str] = set()
    ignored_in_folders = 0

    def add(path: Path) -> None:
        try:
            key = str(path.resolve()).lower()
        except OSError:
            key = str(path).lower()
        if key in seen:
            return
        seen.add(key)
        found.append(path)

    for raw in paths:
        try:
            path = raw.expanduser()
        except Exception:
            path = raw
        if path.is_dir():
            for root, dirs, files in os.walk(path):
                dirs[:] = [name for name in dirs if name not in SKIP_DIRS and not name.startswith(".")]
                for filename in files:
                    child = Path(root) / filename
                    if _is_supported(child):
                        add(child)
                    elif child.is_file():
                        ignored_in_folders += 1
        elif path.is_file():
            if _is_supported(path):
                add(path)
            else:
                notes.append(f"不支持的文件：{path.name}")
        else:
            notes.append(f"找不到：{path}")

    found.sort(key=lambda item: str(item).lower())
    if ignored_in_folders and found:
        notes.append(f"已忽略文件夹中的 {ignored_in_folders} 个非图片文件")
    elif ignored_in_folders and not found:
        notes.append(f"有 {ignored_in_folders} 个文件不是支持的图片格式")
    return found, notes


def _to_working(img: Image.Image) -> Image.Image:
    if img.mode in ("RGB", "RGBA"):
        return img
    if img.mode in ("P", "PA", "LA"):
        return img.convert("RGBA")
    if img.mode == "L":
        return img.convert("RGB")
    if img.mode == "CMYK":
        return img.convert("RGB")
    return img.convert("RGBA")


def _rotate_cw(img: Image.Image, degrees: int) -> Image.Image:
    degrees %= 360
    if degrees == 90:
        return img.transpose(Image.Transpose.ROTATE_270)
    if degrees == 180:
        return img.transpose(Image.Transpose.ROTATE_180)
    if degrees == 270:
        return img.transpose(Image.Transpose.ROTATE_90)
    return img


def oriented_size(size: tuple[int, int], rotation: int) -> tuple[int, int]:
    width, height = size
    if rotation % 180 == 90:
        return height, width
    return width, height


def target_size(src_size: tuple[int, int], edit: EditState) -> tuple[int, int]:
    """导出像素尺寸。src_size 是校正 EXIF 之后、用户旋转之前的尺寸。"""
    ow, oh = oriented_size(src_size, edit.rotation)
    ow = max(1, ow)
    oh = max(1, oh)
    mode = edit.resize_mode
    if mode == "scale":
        scale = (100.0 if edit.scale is None else float(edit.scale)) / 100.0
        return max(1, round(ow * scale)), max(1, round(oh * scale))
    if mode == "width" and edit.width:
        width = max(1, int(edit.width))
        if edit.aspect_lock:
            height = max(1, round(width * oh / ow))
        else:
            height = max(1, int(edit.height or oh))
        return width, height
    if mode == "height" and edit.height:
        height = max(1, int(edit.height))
        if edit.aspect_lock:
            width = max(1, round(height * ow / oh))
        else:
            width = max(1, int(edit.width or ow))
        return width, height
    if mode == "exact":
        return max(1, int(edit.width or ow)), max(1, int(edit.height or oh))
    return ow, oh


def transform_image(
    image: Image.Image,
    edit: EditState,
    full_size: tuple[int, int] | None = None,
) -> Image.Image:
    """旋转、翻转、缩放。预览图可传入 full_size，按原图像素计算比例。"""
    img = _to_working(image)
    img = img.copy()
    rotation = edit.rotation % 360
    if rotation:
        img = _rotate_cw(img, rotation)
    if edit.flip_h:
        img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if edit.flip_v:
        img = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)

    src_w, src_h = full_size or image.size
    tw, th = target_size((src_w, src_h), edit)
    ow, oh = oriented_size((src_w, src_h), rotation)
    if ow <= 0 or oh <= 0 or (tw, th) == (ow, oh):
        return img
    new_w = max(1, round(img.width * (tw / ow)))
    new_h = max(1, round(img.height * (th / oh)))
    if (new_w, new_h) != img.size:
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    return img


def _open_error(exc: Exception) -> ImageReadError:
    if isinstance(exc, Image.DecompressionBombError):
        return ImageReadError("图片像素过多，已拒绝打开")
    if isinstance(exc, UnidentifiedImageError):
        return ImageReadError("无法识别的图片文件")
    return ImageReadError(f"无法读取图片：{exc}")


def load_frames(path: Path) -> tuple[list[Image.Image], list[int], int]:
    try:
        with Image.open(path) as raw:
            count = getattr(raw, "n_frames", 1) or 1
            if count <= 1:
                oriented = ImageOps.exif_transpose(raw)
                return [oriented.copy()], [int(raw.info.get("duration", 0) or 0)], int(raw.info.get("loop", 0) or 0)
            frames: list[Image.Image] = []
            durations: list[int] = []
            for _index in range(count):
                frame = raw.copy()
                frames.append(frame)
                durations.append(int(raw.info.get("duration", 100) or 100))
                if _index < count - 1:
                    raw.seek(_index + 1)
            return frames, durations, int(raw.info.get("loop", 0) or 0)
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError, ValueError) as exc:
        raise _open_error(exc) from exc


def read_first_frame(path: Path) -> Image.Image:
    """预览和缩略图只取第一帧，并校正单帧图片的 EXIF 方向。"""
    try:
        with Image.open(path) as raw:
            count = getattr(raw, "n_frames", 1) or 1
            if count <= 1:
                oriented = ImageOps.exif_transpose(raw)
                frame = oriented.copy()
            else:
                raw.seek(0)
                frame = raw.copy()
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError, ValueError) as exc:
        raise _open_error(exc) from exc
    if frame.size[0] < 1 or frame.size[1] < 1:
        raise ImageReadError("图片尺寸无效")
    return _to_working(frame)


def load_preview_base(path: Path, max_side: int = 1600) -> tuple[Image.Image, tuple[int, int]]:
    frame = read_first_frame(path)
    full = frame.size
    scale = min(1.0, max_side / max(full))
    if scale < 1:
        frame = frame.resize(
            (max(1, round(full[0] * scale)), max(1, round(full[1] * scale))),
            Image.Resampling.BILINEAR,
        )
    return frame, full


def make_thumbnail(path: Path, side: int = 112) -> tuple[Image.Image, tuple[int, int]]:
    frame, full = load_preview_base(path, max_side=side * 2)
    frame.thumbnail((side, side), Image.Resampling.LANCZOS)
    return frame, full


def _flatten_rgb(img: Image.Image) -> Image.Image:
    if img.mode == "RGB":
        return img
    rgba = img.convert("RGBA")
    background = Image.new("RGB", rgba.size, (255, 255, 255))
    background.paste(rgba, mask=rgba.getchannel("A"))
    return background


def _prepare_mode(img: Image.Image, ext: str) -> Image.Image:
    ext = ext.lower().lstrip(".")
    if ext in JPEG_EXTS or ext == "bmp":
        return _flatten_rgb(img)
    if ext == "gif":
        return img.convert("RGBA").convert("P", palette=Image.Palette.ADAPTIVE, colors=255)
    if img.mode in ("RGB", "RGBA"):
        return img
    if img.mode in ("P", "PA", "LA", "L"):
        return img.convert("RGBA")
    return img.convert("RGB")


def pillow_format(ext: str) -> str:
    ext = ext.lower().lstrip(".")
    if ext in JPEG_EXTS:
        return "JPEG"
    if ext in TIFF_EXTS:
        return "TIFF"
    mapping = {"png": "PNG", "webp": "WEBP", "bmp": "BMP", "gif": "GIF"}
    if ext not in mapping:
        raise ImageReadError(f"不支持的导出格式：{ext}")
    return mapping[ext]


def _geometry_requested(edit: EditState) -> bool:
    if edit.rotation % 360 or edit.flip_h or edit.flip_v:
        return True
    if edit.resize_mode == "none":
        return False
    if edit.resize_mode == "scale" and abs(float(edit.scale or 100) - 100) < 0.05:
        return False
    return True


def _same_path(left: Path, right: Path) -> bool:
    try:
        if left.resolve() == right.resolve():
            return True
    except OSError:
        pass
    if sys.platform == "win32":
        return os.path.normcase(str(left)) == os.path.normcase(str(right))
    return False


def allocate_dest(
    output_dir: Path,
    stem: str,
    ext: str,
    source: Path,
    reserved: set[str],
    overwrite: bool,
) -> Path:
    stem = sanitize_stem(stem)
    ext = ext.lower().lstrip(".")
    for index in range(10000):
        filename = f"{stem}.{ext}" if index == 0 else f"{stem}_{index}.{ext}"
        dest = output_dir / filename
        if _same_path(dest, source):
            continue
        try:
            key = str(dest.resolve()).lower()
        except OSError:
            key = str(dest).lower()
        if key in reserved:
            continue
        if dest.exists() and not overwrite:
            continue
        reserved.add(key)
        return dest
    raise ImageReadError("无法生成不冲突的文件名")


def _save_frames(path: Path, frames: list[Image.Image], durations: list[int], loop: int, ext: str) -> str | None:
    ext = ext.lower().lstrip(".")
    note = None
    animated = len(frames) > 1 and ext in {"gif", "webp"}
    if len(frames) > 1 and not animated:
        frames = frames[:1]
        durations = durations[:1]
        note = "动图已按第一帧导出为静图"
    prepared = [_prepare_mode(frame, ext) for frame in frames]
    fmt = pillow_format(ext)
    if animated and fmt == "GIF":
        prepared[0].save(
            path,
            save_all=True,
            append_images=prepared[1:],
            duration=durations or 100,
            loop=loop,
            format="GIF",
            disposal=2,
        )
        return note
    if animated and fmt == "WEBP":
        prepared[0].save(
            path,
            save_all=True,
            append_images=prepared[1:],
            duration=durations or 100,
            loop=loop,
            format="WEBP",
            quality=WEBP_QUALITY,
            method=4,
        )
        return note
    kwargs: dict = {}
    if fmt == "JPEG":
        kwargs = {"quality": JPEG_QUALITY, "optimize": True}
    elif fmt == "PNG":
        kwargs = {"compress_level": 6}
    elif fmt == "WEBP":
        kwargs = {"quality": WEBP_QUALITY, "method": 4}
    elif fmt == "TIFF":
        kwargs = {"compression": "tiff_lzw"}
    prepared[0].save(path, format=fmt, **kwargs)
    return note


def _atomic_save(dest: Path, frames, durations, loop, ext) -> str | None:
    tmp = dest.with_name(dest.name + ".part")
    try:
        note = _save_frames(tmp, frames, durations, loop, ext)
        os.replace(tmp, dest)
        return note
    finally:
        if tmp.exists():
            tmp.unlink()


def export_jobs(
    jobs: list[ExportJob],
    output_dir: Path,
    *,
    overwrite: bool = False,
    on_progress=None,
    should_cancel=None,
) -> ExportReport:
    report = ExportReport(output_dir=output_dir)
    if not output_dir.exists() or not output_dir.is_dir():
        report.failed = len(jobs)
        report.errors.append("输出文件夹不存在")
        return report

    reserved: set[str] = set()
    total = len(jobs)
    for index, job in enumerate(jobs, start=1):
        if should_cancel and should_cancel():
            report.cancelled = True
            break
        label = job.source.name
        if on_progress:
            on_progress(index, total, label)
        try:
            _export_one(job, output_dir, reserved, overwrite, report)
        except Exception as exc:  # 单张失败不影响其余图片
            report.failed += 1
            message = str(exc).strip() or exc.__class__.__name__
            report.errors.append(f"{label}：{message}")
    return report


def _export_one(
    job: ExportJob,
    output_dir: Path,
    reserved: set[str],
    overwrite: bool,
    report: ExportReport,
) -> None:
    edit = job.edit.copy()
    edit.ext = edit.ext.lower().lstrip(".")
    problem = name_problem(edit.stem)
    if problem:
        raise ImageReadError(problem)
    if edit.ext not in SUPPORTED_EXTS:
        raise ImageReadError(f"不支持的导出格式：{edit.ext}")

    dest = allocate_dest(output_dir, edit.stem, edit.ext, job.source, reserved, overwrite)
    same_format = same_container(job.source.suffix, edit.ext)
    if same_format and not _geometry_requested(edit):
        shutil.copy2(job.source, dest)
        report.success += 1
        report.copied += 1
        report.written.append(dest)
        return

    frames, durations, loop = load_frames(job.source)
    full = frames[0].size
    if same_format and not (edit.rotation % 360 or edit.flip_h or edit.flip_v):
        if target_size(full, edit) == full:
            shutil.copy2(job.source, dest)
            report.success += 1
            report.copied += 1
            report.written.append(dest)
            return

    # 动图逐帧处理；静图在 load_frames 里已经校正过 EXIF。
    transformed = []
    for frame in frames:
        size = frame.size
        if len(frames) == 1:
            size = full
        transformed.append(transform_image(frame, edit, full_size=size))
    note = _atomic_save(dest, transformed, durations, loop, edit.ext)
    report.success += 1
    report.encoded += 1
    report.written.append(dest)
    if note:
        report.notes.append(f"{dest.name}：{note}")


def compose_preview(image: Image.Image, box: tuple[int, int]) -> Image.Image:
    max_w = max(1, box[0])
    max_h = max(1, box[1])
    img = image.copy()
    img.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)
    if img.mode == "RGBA":
        board = _checker(img.size)
        board.paste(img, mask=img.getchannel("A"))
        return board
    if img.mode != "RGB":
        return img.convert("RGB")
    return img


def _checker(size: tuple[int, int], cell: int = 12) -> Image.Image:
    width, height = size
    board = Image.new("RGB", size, (22, 25, 32))
    dark = (36, 41, 51)
    draw_row = bytearray()
    for x in range(width):
        color = dark if (x // cell) % 2 == 0 else (22, 25, 32)
        draw_row.extend(color)
    even = bytes(draw_row)
    odd_row = bytearray()
    for x in range(width):
        color = (22, 25, 32) if (x // cell) % 2 == 0 else dark
        odd_row.extend(color)
    odd = bytes(odd_row)
    rows = []
    for y in range(height):
        rows.append(even if (y // cell) % 2 == 0 else odd)
    return Image.frombytes("RGB", size, b"".join(rows))
