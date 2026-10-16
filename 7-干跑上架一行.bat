@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Python venv not found. Please run: uv sync
  pause
  exit /b 1
)
echo === DRY RUN: list ONE row (writes nothing, spends nothing) ===
echo.
echo Leave the login identity blank to let the tool work it out
echo from the sheet's shop name (that is the normal way now).
echo.
set SHOP=
set /p SHOP=Login identity [blank = auto] :
set ROW=
set /p ROW=Excel row number to test (e.g. 32)   :
echo.
if "%SHOP%"=="" (
  echo --- Shop check: auto-detected from the sheet ---
  echo.
  ".venv\Scripts\python.exe" -m temu_bot.cli run --row-index %ROW%
) else (
  echo --- Shop check first: the row shop MUST match the login ---
  echo.
  ".venv\Scripts\python.exe" -m temu_bot.cli run --shop %SHOP% --row-index %ROW%
)
echo.
echo === Dry run finished. Nothing was written. ===
pause
