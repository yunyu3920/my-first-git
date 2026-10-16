@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Python venv not found. Please run: uv sync
  pause
  exit /b 1
)
echo === Collect images into a product table (DRY RUN by default) ===
echo.
echo Pairs:  ID.jpg  +  ID-???.jpg   =  one row
echo If the ID-??? material image is missing, one is GENERATED from the main image.
echo (Pass --no-force-material to go back to skipping those rows.)
echo Output is ALWAYS a new file - an existing table is never overwritten.
echo.
set /p DIR=Image folder :
set /p SHOP=Shop name (e.g. A hat tshirt) :
set /p SPU=Template SPU (e.g. 6255081762) :
echo.
echo --- dry run: only reports, writes nothing ---
echo.
".venv\Scripts\python.exe" -m temu_bot.cli collect --dir "%DIR%" --shop "%SHOP%" --template-spu "%SPU%"
echo.
set /p GO=Write the table now? type Y then Enter (anything else = skip) : 
if /i "%GO%"=="Y" (
  ".venv\Scripts\python.exe" -m temu_bot.cli collect --dir "%DIR%" --shop "%SHOP%" --template-spu "%SPU%" --live
) else (
  echo Skipped. Nothing was written.
)
echo.
pause