@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Python venv not found. Please run: uv sync
  pause
  exit /b 1
)
echo. 
echo !!! IMPORTANT: use the Chrome window that THIS tool opens. !!!
echo     That window has a YELLOW warning bar at the top (--no-sandbox).
echo     Your own everyday Chrome does NOT have that bar.
echo     Do the steps in the YELLOW-BAR window, or nothing gets recorded.
echo.
echo === Step 4 : Record PUBLISH ===
echo.
echo In the YELLOW-BAR window:
echo   1) Products  -  Pending list  -  click the  publish failed  filter
echo   2) Tick ONE row
echo   3) Batch actions  -  Batch publish   (confirm if asked)
echo   4) Come BACK here and press Enter
echo.
echo NOTE: this really submits that ONE product to Temu. Pick one you want live.
echo.
".venv\Scripts\python.exe" -m temu_bot.cli record --shop hat-tshirt
echo.
pause
