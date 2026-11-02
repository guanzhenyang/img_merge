@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul

set "PY=python"
where py >nul 2>&1 && set "PY=py -3"

%PY% -c "import sys; raise SystemExit(0 if sys.version_info>=(3,11) else 1)"
if errorlevel 1 (
  echo 需要 Python 3.11 或更高版本。请从 python.org 安装，并勾选 tcl/tk 与 pip。
  exit /b 1
)

%PY% -m pip install -r requirements-build.txt
if errorlevel 1 exit /b 1

%PY% -m PyInstaller --noconfirm --clean image_workbench.spec
if errorlevel 1 exit /b 1

echo.
echo 已生成: dist\图片工坊\图片工坊.exe
echo 请保留整个 dist\图片工坊 文件夹，不要只拷贝 exe。
