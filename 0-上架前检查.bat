@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Python venv not found. Please run: uv sync
  pause
  exit /b 1
)
echo === PREFLIGHT: information table + image folder ===
echo.
echo The tool needs TWO inputs:
echo   1) the information table (config: project.table)
echo   2) the image folder   (column 1 of the table, or type it below)
echo.
set /p DIR=Image folder (leave empty = use column 1 of the table) : 
echo.
if "%DIR%"=="" (
  ".venv\Scripts\python.exe" -m temu_bot.cli preflight
) else (
  ".venv\Scripts\python.exe" -m temu_bot.cli preflight --images-dir "%DIR%"
)
echo.
pause