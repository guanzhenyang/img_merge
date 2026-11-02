#!/usr/bin/env bash
# 在 macOS 上打包 图片工坊.app。必须在 Mac 上运行，Linux / Windows 无法交叉编译出可双击的 .app。
set -euo pipefail
cd "$(dirname "$0")"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "图片工坊的 Mac 应用必须在 macOS 上打包。"
  echo "当前系统：$(uname -s)。请把源码拷到 Mac 后执行："
  echo "  chmod +x build_macos.sh && ./build_macos.sh"
  exit 1
fi

PY="${PYTHON:-python3}"
if [[ -x .venv/bin/python ]]; then
  PY=".venv/bin/python"
fi

if ! "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info>=(3,11) else 1)'; then
  echo "需要 Python 3.11 或更高版本，并带有 Tcl/Tk。可从 python.org 安装。"
  exit 1
fi

if [[ ! -f assets/icon.png ]]; then
  echo "缺少 assets/icon.png"
  exit 1
fi

# iconutil 需要一套固定文件名的 iconset。源图是 256，放大到 512 仅用于打包图标。
ICONSET="assets/icon.iconset"
rm -rf "$ICONSET"
mkdir -p "$ICONSET"
sips -z 16 16 assets/icon.png --out "$ICONSET/icon_16x16.png" >/dev/null
sips -z 32 32 assets/icon.png --out "$ICONSET/icon_16x16@2x.png" >/dev/null
sips -z 32 32 assets/icon.png --out "$ICONSET/icon_32x32.png" >/dev/null
sips -z 64 64 assets/icon.png --out "$ICONSET/icon_32x32@2x.png" >/dev/null
sips -z 128 128 assets/icon.png --out "$ICONSET/icon_128x128.png" >/dev/null
sips -z 256 256 assets/icon.png --out "$ICONSET/icon_128x128@2x.png" >/dev/null
sips -z 256 256 assets/icon.png --out "$ICONSET/icon_256x256.png" >/dev/null
sips -z 512 512 assets/icon.png --out "$ICONSET/icon_256x256@2x.png" >/dev/null
sips -z 512 512 assets/icon.png --out "$ICONSET/icon_512x512.png" >/dev/null
sips -z 1024 1024 assets/icon.png --out "$ICONSET/icon_512x512@2x.png" >/dev/null
iconutil -c icns "$ICONSET" -o assets/icon.icns
rm -rf "$ICONSET"

"$PY" -m pip install -r requirements-build.txt
"$PY" -m PyInstaller --noconfirm --clean image_workbench.spec

echo
echo "已生成: dist/图片工坊.app"
echo "这是未签名的应用，没有做 Apple 公证。"
echo "若系统提示无法打开，请右键选择「打开」，或执行："
echo "  xattr -dr com.apple.quarantine \"dist/图片工坊.app\""
