@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Python venv not found. Please run: uv sync
  pause
  exit /b 1
)
echo === Capture the page that is stuck ===
echo.
echo In the browser window:
echo   1) Go to DianXiaoMi - Products (online / pending list)
echo   2) Find the copied product you CANNOT open
echo   3) Click it - reproduce the problem
echo   4) Come BACK here and press Enter to capture
echo.
echo You may press Enter several times to capture several pages.
echo Type  q  then Enter to finish.
echo.
echo NOTE: page dialogs are recorded AND auto-confirmed (Playwright cancels
echo       them by default, which looks like "clicking does nothing").
echo.
".venv\Scripts\python.exe" -m temu_bot.cli probe --shop hat-tshirt --interactive --label stuck
echo.
pause
