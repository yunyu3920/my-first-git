@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Python venv not found. Please run: uv sync
  pause
  exit /b 1
)
echo === Step 3/3 : Record ONE real listing flow ===
echo.
echo Do ONE product manually in the browser window:
echo   1) Products - find the template product (SPU 8909735440)
echo   2) Click Copy, open the copied product edit page
echo   3) Change the title, upload main image + material image, fill SKU code
echo   4) Click Save
echo.
echo Keep this window open while recording. Press Enter here when finished.
echo.
".venv\Scripts\python.exe" -m temu_bot.cli record --shop hat-tshirt
echo.
pause
