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
echo === Step 5 : Record COPY (move product to a shop) ===
echo.
echo WARNING: copying consumes MiDou credits - you had only 1 left.
echo          Skip this step if you do not want to spend it.
echo.
echo In the YELLOW-BAR window:
echo   1) Products  -  Online list  -  find the template product
echo   2) Click  Copy   -  the "select shops" dialog appears
echo   3) Tick the target shop, click OK
echo   4) Wait for the success toast, then come BACK here
echo.
".venv\Scripts\python.exe" -m temu_bot.cli record --shop hat-tshirt
echo.
pause
