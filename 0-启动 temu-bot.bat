@echo off
chcp 65001 >nul
cd /d "%~dp0"
title temu-bot 控制台
set PYTHONIOENCODING=utf-8

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo   [错误] 没找到 Python 虚拟环境。
  echo   请先在项目目录执行一次：  uv sync
  echo.
  pause
  exit /b 1
)

echo.
echo   ==============================================================
echo.
echo                  t e m u - b o t     控   制   台
echo.
echo   ==============================================================
echo.
echo     双击这个文件就够了：
echo.
echo       1. 起一个只在本机跑的服务（127.0.0.1:8765，别的机器访问不到）
echo       2. 浏览器会自动打开控制台
echo       3. 这个黑窗口就是程序本体，别关它
echo.
echo     要停：关掉这个窗口，或者在窗口里按 Ctrl+C
echo.
echo   ==============================================================
echo.

".venv\Scripts\python.exe" -m temu_bot.cli serve --port 8765

echo.
echo   服务已停止。
pause
