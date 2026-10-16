@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Python venv not found. Please run: uv sync
  pause
  exit /b 1
)
echo === Record the EDIT + SAVE flow ===
echo.
echo In the browser window do ALL of these on ONE product:
echo   1) Products - pending list - open one product (click Edit)
echo   2) Change the title (any change)
echo   3) Replace the MAIN image with any local picture
echo   4) Click SAVE   -  do NOT click Publish
echo   5) Wait until it says saved, then come BACK here
echo.
echo Keep this window open. Press Enter here when finished.
echo.
".venv\Scripts\python.exe" -m temu_bot.cli record --shop hat-tshirt
echo.
pause
