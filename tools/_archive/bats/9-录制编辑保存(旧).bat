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
echo !!! Use the Chrome window THIS tool opens (YELLOW bar at top) !!!
echo.
echo === Step 3 : Record EDIT + SAVE ===
echo.
echo The editor opens BY ITSELF. In the YELLOW-BAR window:
echo   1) Change the TITLE
echo   2) Replace the MAIN image with any local picture
echo   3) Click  S A V E   and WAIT for the success toast
echo        -  do NOT click Publish
echo   4) Only AFTER it says saved, come back here and press Enter
echo.
echo Watch this window: it prints "recorded N requests" while you work.
echo If you press Enter BEFORE clicking Save, the save request is missed.
echo.
".venv\Scripts\python.exe" -m temu_bot.cli record --shop hat-tshirt --pick 1
echo.
pause
