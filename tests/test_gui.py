"""界面流程：导入、预览编辑、导出。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from app.imaging import target_size


def _widgets_text(root) -> str:
    chunks: list[str] = []

    def walk(widget):
        try:
            text = widget.cget("text")
        except Exception:
            text = ""
        if isinstance(text, str) and text:
            chunks.append(text)
        for child in widget.winfo_children():
            walk(child)

    walk(root)
    return "\n".join(chunks)


class GuiFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["IMAGE_WORKBENCH_CONFIG"] = str(Path(self.tmp.name) / "config")
        self.app = None
        from app.ui import WorkbenchApp

        self.app = WorkbenchApp()

    def tearDown(self):
        if self.app is not None:
            self.app._closing = True
            try:
                self.app._thumb_queue.put_nowait(None)
            except Exception:
                pass
            try:
                self.app.destroy()
            except Exception:
                pass
        self.tmp.cleanup()

    def test_labels_and_edit_export_flow(self):
        app = self.app
        app.update()
        text = _widgets_text(app)
        for label in ("添加文件", "添加文件夹", "应用到当前图片", "导出全部", "锁定宽高比", "顺时针 90°", "水平翻转", "批量分辨率", "拉伸到精确尺寸", "按宽度等比", "应用到选中", "应用到全部"):
            self.assertIn(label, text)
        self.assertEqual(app.title(), "图片工坊")

        root = Path(self.tmp.name)
        folder = root / "photos" / "nested"
        folder.mkdir(parents=True)
        Image.new("RGB", (40, 20), (240, 20, 20)).save(folder / "wide.png")
        Image.new("RGB", (30, 30), (20, 40, 200)).save(root / "photos" / "square.png")
        (root / "photos" / "notes.txt").write_text("skip", encoding="utf-8")

        app.import_paths_blocking([root / "photos"])
        app.wait_idle()
        self.assertEqual(len(app.items), 2)
        self.assertIsNotNone(app.current.original_size)

        app.select_index(0, interactive=False)
        app._rotate(90)
        app.scale_var.set("50")
        app._commit_scale_entry()
        app.stem_var.set("out-one")
        app._on_stem()
        self.assertTrue(app._is_dirty())
        self.assertTrue(app.apply_current())
        self.assertFalse(app._is_dirty())
        self.assertEqual(app.current.edit.stem, "out-one")
        self.assertEqual(app.current.edit.rotation, 90)
        self.assertEqual(target_size((40, 20), app.current.edit), (10, 20))

        self.assertTrue(app.apply_to_all(confirm=False))
        stems = sorted(item.edit.stem for item in app.items)
        self.assertEqual(stems, ["out-one", "square"])
        for item in app.items:
            self.assertEqual(item.edit.rotation, 90)
            self.assertEqual(item.edit.resize_mode, "scale")
            self.assertEqual(item.edit.scale, 50)

        out = root / "out"
        out.mkdir()
        app.output_var.set(str(out))
        report = app.export_all(interactive=False)
        self.assertIsNotNone(report)
        self.assertEqual(report.failed, 0, report.errors)
        self.assertEqual(report.success, 2)
        saved = {path.name: Image.open(path).size for path in report.written}
        self.assertEqual(saved["out-one.png"], (10, 20))
        self.assertEqual(saved["square.png"], (15, 15))
        self.assertTrue((folder / "wide.png").exists())
        self.assertEqual(Image.open(folder / "wide.png").size, (40, 20))

    def test_batch_resolution_stretch_and_proportional_width(self):
        app = self.app
        root = Path(self.tmp.name)
        wide = root / "wide.png"
        tall = root / "tall.png"
        Image.new("RGB", (40, 20), (10, 20, 30)).save(wide)
        Image.new("RGB", (20, 40), (30, 10, 10)).save(tall)
        app.import_paths_blocking([wide, tall])
        app.wait_idle()
        self.assertEqual(len(app.items), 2)

        app.batch_width_var.set("20")
        app.batch_height_var.set("10")
        app._set_batch_mode("stretch")
        self.assertTrue(app.apply_batch_resolution(scope="all", confirm=False))
        for item in app.items:
            self.assertEqual(item.edit.resize_mode, "exact")
            self.assertEqual((item.edit.width, item.edit.height), (20, 10))
            self.assertFalse(item.edit.aspect_lock)

        out = root / "stretched"
        out.mkdir()
        app.output_var.set(str(out))
        report = app.export_all(interactive=False)
        self.assertEqual(report.failed, 0, report.errors)
        for path in report.written:
            with Image.open(path) as saved:
                self.assertEqual(saved.size, (20, 10))

        app._set_batch_mode("width")
        app.batch_width_var.set("20")
        self.assertTrue(app.apply_batch_resolution(scope="all", confirm=False))
        sizes = {item.source.name: target_size(item.original_size, item.edit) for item in app.items}
        self.assertEqual(sizes["wide.png"], (20, 10))
        self.assertEqual(sizes["tall.png"], (20, 40))

        app.selected_ids = {id(app.items[0])}
        app.batch_width_var.set("10")
        app.batch_height_var.set("8")
        app._set_batch_mode("stretch")
        self.assertTrue(app.apply_batch_resolution(scope="selected", confirm=False))
        self.assertEqual((app.items[0].edit.width, app.items[0].edit.height), (10, 8))
        self.assertEqual(app.items[0].edit.resize_mode, "exact")
        self.assertEqual(app.items[1].edit.resize_mode, "width")
        self.assertEqual(app.items[1].edit.width, 20)

    def test_drop_parser_uses_tk_splitlist(self):
        paths = __import__("app.paths", fromlist=["parse_dropped_paths"]).parse_dropped_paths(
            "{/tmp/my file.jpg} /tmp/plain.png",
            self.app.tk.splitlist,
        )
        self.assertEqual([path.name for path in paths], ["my file.jpg", "plain.png"])


if __name__ == "__main__":
    unittest.main()
