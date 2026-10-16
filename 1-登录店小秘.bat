@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Python venv not found. Please run: uv sync
  pause
  exit /b 1
)
echo === Step 1/3 : Login to DianXiaoMi ===
echo.
echo A browser window will open.
echo   - Log in to DianXiaoMi in that window
echo   - Then come BACK to this window and press Enter
echo.
".venv\Scripts\python.exe" -m temu_bot.cli login --shop hat-tshirt
echo.
echo === Done. Next step: run the launcher whose name starts with 2- ===
pause
