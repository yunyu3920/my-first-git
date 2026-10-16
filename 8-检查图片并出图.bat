@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Python venv not found. Please run: uv sync
  pause
  exit /b 1
)
echo === Image check / prep (original files are NEVER modified) ===
echo.
set /p ROW=Excel row number (e.g. 32) : 
echo.
echo --- 1) check only (read-only) ---
echo.
".venv\Scripts\python.exe" -m temu_bot.cli images check --row-index %ROW%
echo.
echo --- 2) prep: write compliant images into work\ (dry-run by default) ---
echo.
set /p GO=Write the images now? type Y then Enter (anything else = skip) : 
if /i "%GO%"=="Y" (
  ".venv\Scripts\python.exe" -m temu_bot.cli images prep --row-index %ROW% --live
) else (
  echo Skipped. Nothing was written.
)
echo.
pause