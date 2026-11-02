# -*- mode: python ; coding: utf-8 -*-
# 在 Windows 上执行 build_windows.bat / build_windows.ps1。
# PyInstaller 会打进“当前这台电脑”的 Python 和 tkdnd，不能在 Linux 上交叉编译出可用的 exe。

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None
root = Path(SPECPATH)
app_name = "图片工坊"
one_file = False  # 改为 True 可打成单个 exe / Mac 单文件，启动会稍慢
if sys.platform == "darwin":
    icns = root / "assets" / "icon.icns"
    icon_file = str(icns if icns.exists() else root / "assets" / "icon.png")
else:
    icon_file = str(root / "assets" / "icon.ico")

datas = []
datas += collect_data_files("customtkinter")
datas += [(str(root / "assets"), "assets")]

hiddenimports = [
    "customtkinter",
    "tkinterdnd2",
    "tkinterdnd2.TkinterDnD",
    "PIL.Image",
    "PIL.ImageOps",
    "PIL.ImageDraw",
    "PIL.ImageTk",
    "PIL.ImageFilter",
    "PIL.ExifTags",
    "PIL._tkinter_finder",
]

a = Analysis(
    [str(root / "run.py")],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "unittest"],
    noarchive=False,
    cipher=block_cipher,
)
pyz = PYZ(a.pure, cipher=block_cipher)

if one_file:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name=app_name,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        icon=icon_file,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=app_name,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        icon=icon_file,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name=app_name,
    )

# macOS 上再包一层 .app。Windows 构建不会走进这个分支。
if sys.platform == "darwin":
    bundle_target = exe if one_file else coll
    app = BUNDLE(
        bundle_target,
        name="图片工坊.app",
        icon=icon_file,
        bundle_identifier="local.imageworkbench",
        info_plist={
            "CFBundleName": "图片工坊",
            "CFBundleDisplayName": "图片工坊",
            "NSHighResolutionCapable": True,
        },
    )
