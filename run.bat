@echo off
setlocal
cd /d "%~dp0"
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
%PY% run.py
if errorlevel 1 (
  echo.
  echo 如果提示缺少模块，请先执行:
  echo   %PY% -m pip install -r requirements.txt
  exit /b 1
)
