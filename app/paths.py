"""路径、文件名和本机设置。"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

from app.model import WINDOWS_RESERVED


def bundle_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def asset_path(name: str) -> Path:
    return bundle_root() / "assets" / name


def config_dir() -> Path:
    override = os.environ.get("IMAGE_WORKBENCH_CONFIG")
    if override:
        path = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
        path = base / "image-workbench"
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / "image-workbench"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        path = base / "image-workbench"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_settings() -> dict:
    path = config_dir() / "settings.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(data: dict) -> None:
    path = config_dir() / "settings.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def name_problem(stem: str) -> str | None:
    if stem.strip() == "":
        return "文件名不能为空"
    if stem != stem.strip():
        return "文件名首尾不能有空格"
    if re.search(r'[<>:"/\\|?*\x00-\x1f]', stem):
        return "文件名不能包含 \\ / : * ? \" < > | 或控制字符"
    if stem.endswith("."):
        return "文件名不能以点结尾"
    if stem.upper() in WINDOWS_RESERVED:
        return "该名称是 Windows 保留字，请换一个"
    if len(stem) > 150:
        return "文件名请控制在 150 个字符以内"
    return None


def sanitize_stem(stem: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).strip().rstrip(". ")
    if not cleaned:
        return "未命名"
    if cleaned.upper() in WINDOWS_RESERVED:
        cleaned = f"_{cleaned}"
    return cleaned[:150]


def same_container(ext_a: str, ext_b: str) -> bool:
    from app.model import JPEG_EXTS, TIFF_EXTS

    a = ext_a.lower().lstrip(".")
    b = ext_b.lower().lstrip(".")
    if a in JPEG_EXTS and b in JPEG_EXTS:
        return True
    if a in TIFF_EXTS and b in TIFF_EXTS:
        return True
    return a == b


def fallback_split_dnd(data: str) -> list[str]:
    """Tcl 风格的拖放列表解析，供 splitlist 不可用时使用。"""
    parts: list[str] = []
    buf: list[str] = []
    brace = 0
    for ch in data:
        if ch == "{" and brace == 0 and not buf:
            brace = 1
            continue
        if ch == "{" and brace > 0:
            brace += 1
            buf.append(ch)
            continue
        if ch == "}" and brace > 0:
            brace -= 1
            if brace == 0:
                parts.append("".join(buf))
                buf = []
            else:
                buf.append(ch)
            continue
        if ch == " " and brace == 0:
            if buf:
                parts.append("".join(buf))
                buf = []
            continue
        buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return parts


def normalize_dropped_path(raw: str) -> str:
    text = raw.strip().strip('"')
    if text.lower().startswith("file://"):
        parsed = urlparse(text)
        text = unquote(parsed.path)
        if re.match(r"^/[A-Za-z]:", text):
            text = text[1:]
    return text


def parse_dropped_paths(data: str, splitlist=None) -> list[Path]:
    parts: list[str]
    if splitlist is not None:
        try:
            parts = [str(part) for part in splitlist(data)]
        except Exception:
            parts = fallback_split_dnd(data)
    else:
        parts = fallback_split_dnd(data)
    paths: list[Path] = []
    for part in parts:
        text = normalize_dropped_path(part)
        if text:
            paths.append(Path(text))
    return paths


def format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    if size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{size / (1024 * 1024 * 1024):.2f} GB"


def ellipsize(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)] + "…"
