"""图片扫描、变换和导出。"""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from app.imaging import (
    allocate_dest,
    collect_images,
    export_jobs,
    load_frames,
    read_first_frame,
    target_size,
    transform_image,
)
from app.model import SUPPORTED_EXTS, EditState, ExportJob, apply_resolution
from app.paths import fallback_split_dnd, name_problem, normalize_dropped_path, parse_dropped_paths


def _edit(stem="a", ext="png", **kwargs) -> EditState:
    return EditState(stem=stem, ext=ext, **kwargs)


class CollectTests(unittest.TestCase):
    def test_recursive_folder_skips_unrelated_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.JPG").write_bytes(b"")
            # 空文件不是合法图片，但扩展名会被收集；这里用真实小图。
            Image.new("RGB", (4, 4), "red").save(root / "a.JPG")
            nested = root / "sub"
            nested.mkdir()
            Image.new("RGB", (4, 4), "blue").save(nested / "b.png")
            (nested / "notes.txt").write_text("hello", encoding="utf-8")
            mac = root / "__MACOSX"
            mac.mkdir()
            Image.new("RGB", (4, 4), "green").save(mac / "hidden.jpg")
            dot = root / ".secret"
            dot.mkdir()
            Image.new("RGB", (4, 4), "white").save(dot / "skip.png")

            found, notes = collect_images([root])
            names = sorted(path.name for path in found)
            self.assertEqual(names, ["a.JPG", "b.png"])
            self.assertTrue(any("非图片" in note for note in notes))

    def test_explicit_unsupported_file_is_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = Path(tmp) / "readme.txt"
            text.write_text("x", encoding="utf-8")
            found, notes = collect_images([text, Path(tmp) / "missing.png"])
            self.assertEqual(found, [])
            self.assertTrue(any("readme.txt" in note for note in notes))
            self.assertTrue(any("找不到" in note for note in notes))

    def test_duplicate_paths_collapse(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.png"
            Image.new("RGB", (2, 2)).save(path)
            found, _notes = collect_images([path, path])
            self.assertEqual(len(found), 1)


class NameTests(unittest.TestCase):
    def test_rejects_unsafe_windows_names(self):
        self.assertIsNone(name_problem("假期照片"))
        self.assertIsNotNone(name_problem(""))
        self.assertIsNotNone(name_problem("a/b"))
        self.assertIsNotNone(name_problem("CON"))
        self.assertIsNotNone(name_problem("photo."))
        self.assertIsNotNone(name_problem(" photo"))

    def test_drop_parser_keeps_spaces(self):
        parts = fallback_split_dnd("{/tmp/my file.jpg} /tmp/plain.png")
        self.assertEqual(parts, ["/tmp/my file.jpg", "/tmp/plain.png"])
        paths = parse_dropped_paths("{/tmp/my file.jpg} /tmp/plain.png")
        self.assertEqual(paths[0].name, "my file.jpg")
        self.assertEqual(normalize_dropped_path("file:///C:/Users/a%20b/c.jpg"), "C:/Users/a b/c.jpg")


class TransformTests(unittest.TestCase):
    def test_clockwise_rotation_moves_corner_pixel(self):
        img = Image.new("RGB", (3, 2), "white")
        img.putpixel((0, 0), (255, 0, 0))
        out = transform_image(img, _edit(rotation=90))
        self.assertEqual(out.size, (2, 3))
        self.assertEqual(out.getpixel((1, 0)), (255, 0, 0))

    def test_horizontal_flip(self):
        img = Image.new("RGB", (3, 2), "white")
        img.putpixel((0, 0), (255, 0, 0))
        out = transform_image(img, _edit(flip_h=True))
        self.assertEqual(out.getpixel((2, 0)), (255, 0, 0))

    def test_locked_resize_modes(self):
        size = (4, 2)
        self.assertEqual(target_size(size, _edit(resize_mode="scale", scale=50)), (2, 1))
        self.assertEqual(target_size(size, _edit(resize_mode="width", width=10, aspect_lock=True)), (10, 5))
        self.assertEqual(target_size(size, _edit(resize_mode="height", height=10, aspect_lock=True)), (20, 10))
        self.assertEqual(target_size(size, _edit(resize_mode="exact", width=8, height=2, aspect_lock=False)), (8, 2))
        rotated = _edit(rotation=90, resize_mode="width", width=4, aspect_lock=True)
        self.assertEqual(target_size(size, rotated), (4, 8))

    def test_exif_orientation_is_applied_for_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "turned.jpg"
            img = Image.new("RGB", (6, 2), (1, 2, 3))
            exif = Image.Exif()
            exif[274] = 6
            img.save(path, exif=exif, quality=95)
            frame = read_first_frame(path)
            self.assertEqual(frame.size, (2, 6))


class BatchResolutionTests(unittest.TestCase):
    def test_stretch_uses_the_same_box_for_every_aspect(self):
        wide = _edit(rotation=90)
        tall = _edit("b", "jpg")
        apply_resolution(wide, mode="stretch", width=900, height=600)
        apply_resolution(tall, mode="stretch", width=900, height=600)
        self.assertEqual(target_size((40, 20), wide), (900, 600))
        self.assertEqual(target_size((20, 50), tall), (900, 600))
        self.assertEqual(wide.resize_mode, "exact")
        self.assertFalse(wide.aspect_lock)
        self.assertEqual(wide.rotation, 90)
        self.assertEqual(wide.stem, "a")

    def test_width_mode_scales_each_image(self):
        wide = _edit()
        square = _edit("b", "png", rotation=90)
        apply_resolution(wide, mode="width", width=900)
        apply_resolution(square, mode="width", width=900)
        self.assertEqual(target_size((40, 20), wide), (900, 450))
        # 20×40 旋转 90° 后是 40×20，宽度 900 时高度为 450。
        self.assertEqual(square.rotation, 90)
        self.assertEqual(target_size((20, 40), square), (900, 450))
        portrait = _edit("c", "png")
        apply_resolution(portrait, mode="width", width=900)
        self.assertEqual(target_size((20, 40), portrait), (900, 1800))
        self.assertTrue(portrait.aspect_lock)
        self.assertIsNone(portrait.height)


class ExportTests(unittest.TestCase):
    def test_rename_copies_bytes_without_touching_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "orig.jpg"
            Image.new("RGB", (8, 6), (20, 40, 60)).save(src, quality=90)
            before = src.read_bytes()
            out = root / "out"
            out.mkdir()
            report = export_jobs([ExportJob(src, _edit("renamed", "jpeg"))], out)
            self.assertEqual(report.success, 1)
            self.assertEqual(report.copied, 1)
            self.assertEqual(report.failed, 0)
            dest = out / "renamed.jpeg"
            self.assertEqual(dest.read_bytes(), before)
            self.assertEqual(src.read_bytes(), before)

    def test_resize_and_rotate_writes_a_new_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "wide.png"
            Image.new("RGB", (40, 20), (10, 20, 30)).save(src)
            out = root / "out"
            out.mkdir()
            edit = _edit("wide", "png", rotation=90, resize_mode="scale", scale=50)
            report = export_jobs([ExportJob(src, edit)], out)
            self.assertEqual(report.encoded, 1)
            saved = Image.open(out / "wide.png")
            self.assertEqual(saved.size, (10, 20))
            saved.close()
            with Image.open(src) as original:
                self.assertEqual(original.size, (40, 20))

    def test_does_not_overwrite_source_or_existing_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "photo.png"
            Image.new("RGB", (6, 4), "red").save(src)
            original = src.read_bytes()
            existing = root / "other.png"
            Image.new("RGB", (6, 4), "blue").save(existing)
            blue = existing.read_bytes()
            report = export_jobs(
                [
                    ExportJob(src, _edit("photo", "png")),
                    ExportJob(src, _edit("other", "png")),
                ],
                root,
                overwrite=False,
            )
            self.assertEqual(report.success, 2)
            self.assertEqual(src.read_bytes(), original)
            self.assertEqual(existing.read_bytes(), blue)
            written = {path.name for path in report.written}
            self.assertIn("photo_1.png", written)
            self.assertIn("other_1.png", written)

    def test_overwrite_replaces_output_but_not_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "photo.png"
            image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            for y in range(64):
                for x in range(32):
                    image.putpixel((x, y), (220, 20, 20, 255))
            image.save(src)
            original = src.read_bytes()
            out = root / "out"
            out.mkdir()
            other = out / "flat.jpg"
            Image.new("RGB", (8, 8), "blue").save(other, quality=80)
            report = export_jobs([ExportJob(src, _edit("flat", "jpg"))], out, overwrite=True)
            self.assertEqual(report.success, 1)
            self.assertEqual(src.read_bytes(), original)
            with Image.open(other) as saved:
                self.assertEqual(saved.mode, "RGB")
                self.assertEqual(saved.size, (64, 64))
                self.assertGreater(saved.getpixel((8, 32))[0], 150)
                self.assertGreater(saved.getpixel((56, 32))[0], 240)
                self.assertGreater(saved.getpixel((56, 32))[1], 240)

    def test_animated_gif_keeps_frames_when_rotated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "anim.gif"
            first = Image.new("RGB", (8, 4), (255, 0, 0))
            second = Image.new("RGB", (8, 4), (0, 255, 0))
            first.save(src, save_all=True, append_images=[second], duration=[80, 120], loop=0)
            out = root / "out"
            out.mkdir()
            report = export_jobs([ExportJob(src, _edit("anim", "gif", rotation=90))], out)
            self.assertEqual(report.failed, 0, report.errors)
            frames, durations, _loop = load_frames(out / "anim.gif")
            self.assertEqual(len(frames), 2)
            self.assertEqual(frames[0].size, (4, 8))
            self.assertEqual(frames[1].size, (4, 8))
            self.assertEqual(len(durations), 2)

    def test_gif_to_jpeg_uses_first_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "anim.gif"
            Image.new("RGB", (6, 4), "red").save(
                src,
                save_all=True,
                append_images=[Image.new("RGB", (6, 4), "blue")],
                duration=50,
                loop=0,
            )
            out = root / "out"
            out.mkdir()
            report = export_jobs([ExportJob(src, _edit("still", "jpg"))], out)
            self.assertEqual(report.success, 1)
            self.assertTrue(any("第一帧" in note for note in report.notes))
            self.assertEqual(Image.open(out / "still.jpg").size, (6, 4))

    def test_allocate_dest_avoids_source_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "a.png"
            src.write_bytes(b"x")
            dest = allocate_dest(root, "a", "png", src, set(), overwrite=True)
            self.assertNotEqual(dest.resolve(), src.resolve())
            self.assertTrue(dest.name.startswith("a_"))

    def test_supported_extensions_cover_the_brief(self):
        for ext in ("jpg", "jpeg", "png", "webp", "bmp", "gif", "tif", "tiff"):
            self.assertIn(ext, SUPPORTED_EXTS)

    def test_webp_roundtrip_when_encoder_exists(self):
        try:
            Image.new("RGB", (2, 2)).save(io.BytesIO(), format="WEBP")
        except Exception:
            self.skipTest("当前 Pillow 没有 WebP 编码器")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "a.png"
            Image.new("RGB", (12, 8), (8, 9, 10)).save(src)
            out = root / "out"
            out.mkdir()
            report = export_jobs([ExportJob(src, _edit("a", "webp", resize_mode="scale", scale=50))], out)
            self.assertEqual(report.failed, 0, report.errors)
            self.assertEqual(Image.open(out / "a.webp").size, (6, 4))


if __name__ == "__main__":
    unittest.main()
