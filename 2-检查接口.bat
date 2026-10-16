@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Python venv not found. Please run: uv sync
  pause
  exit /b 1
)
echo === Step 2/3 : Check API session ===
echo.
".venv\Scripts\python.exe" -m temu_bot.cli session check --shop hat-tshirt
echo.
echo === If it says the session expired, run the 1- launcher again ===
echo === Otherwise next step: run the launcher whose name starts with 3- ===
pause
