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
echo === Step 3 : Record SAVE (capture the exact payload) ===
echo.
echo The editor opens BY ITSELF. In the YELLOW-BAR window just:
echo   1) Click  S A V E      (no need to change anything)
echo   2) WAIT until it says  saved successfully
echo   3) Only THEN come back here and press Enter
echo.
echo Tips:
echo   - do NOT click Publish
echo   - if you press Enter BEFORE saving, the request is missed again
echo   - watch this window: the counter should jump after saving
echo.
".venv\Scripts\python.exe" -m temu_bot.cli record --shop hat-tshirt --pick 1
echo.
pause
