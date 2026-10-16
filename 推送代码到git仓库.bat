@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 推送 temu-bot 到 git 仓库
set PYTHONIOENCODING=utf-8

echo.
echo   ==============================================================
echo.
echo              把 temu-bot 推送到你的 git 仓库
echo.
echo   ==============================================================
echo.

rem ---------------------------------------------------------- 1. 找 git
rem 本机没装 git 的话，用 Cherry Studio 自带的那一份（路径写死在下面）。
set "GIT="
where git >nul 2>nul
if not errorlevel 1 set "GIT=git"
if not "%GIT%"=="" goto got_git

set "BUNDLED=D:\AIdeepseek\Cherry Studio\resources\app.asar.unpacked\resources\binaries\win32-x64\git\cmd\git.exe"
if exist "%BUNDLED%" set "GIT=%BUNDLED%"
if not "%GIT%"=="" goto got_git

echo   [x] 这台电脑上没有 git（或者它不在 PATH 里），推不了。
echo.
echo   两条路，选一条：
echo.
echo     A. 装一个 Git for Windows（以后在哪儿都能用 git）
echo        https://git-scm.com/download/win
echo        一路下一步装完，把这个窗口关掉，再双击我一次就行。
echo.
echo     B. 用图形界面，不碰命令行
echo        https://desktop.github.com
echo        装完选 File - Add local repository - 选 D:\temu_bot
echo        再点 Publish repository。
echo.
pause
exit /b 1

:got_git
echo   [1/4] git 就绪：
"%GIT%" --version
echo         用的是：%GIT%

rem ------------------------------------------------------ 2. 提交者信息
echo.
echo   [2/4] 这个仓库里记的提交者（现在写的是占位的）：
echo         名字：
"%GIT%" config user.name
echo         邮箱：
"%GIT%" config user.email
echo.
echo         想换成你自己的就现在换 —— 这样 GitHub 上才把提交算到你头上。
echo         不想换就直接回车跳过。
echo.
set "NAME="
set /p "NAME=         你的名字（回车 = 跳过）："
if "%NAME%"=="" goto skip_author
set "EMAIL="
set /p "EMAIL=         你的邮箱（回车 = 跳过）："
if "%EMAIL%"=="" goto skip_author
"%GIT%" config user.name "%NAME%"
"%GIT%" config user.email "%EMAIL%"
"%GIT%" commit --amend --reset-author --no-edit
echo         已改成 %NAME% ^<%EMAIL%^>
goto remote

:skip_author
echo         跳过，保持原样。

rem ---------------------------------------------------------- 3. 远程
:remote
echo.
echo   [3/4] 接上远程仓库
echo.
echo         默认地址：https://github.com/yunyu3920/my-first-git.git
echo         直接回车用这个，或者现在粘贴你自己的地址。
echo.
set "URL="
set /p "URL=         仓库地址："
if "%URL%"=="" set "URL=https://github.com/yunyu3920/my-first-git.git"

"%GIT%" remote get-url origin >nul 2>nul
if errorlevel 1 goto add_remote
"%GIT%" remote set-url origin "%URL%"
echo         已把 origin 改成：%URL%
goto branch

:add_remote
"%GIT%" remote add origin "%URL%"
echo         已加上 origin：%URL%

:branch
"%GIT%" branch -M main

rem ---------------------------------------------------------- 4. 推送
echo.
echo   [4/4] 推送中……
echo.
echo         要是弹出登录窗口，登的是 **你自己的 git 账号**：
echo           GitHub 现在不收密码 —— 走浏览器授权，或者填 Personal Access Token
echo           用 Gitee 的话，填账号密码就行
echo.
echo   ------------------------------------------------------------
"%GIT%" push -u origin main
echo   ------------------------------------------------------------
echo.

if not errorlevel 1 goto pushed

rem ---- 推送被拒：多半是建仓库时勾了 Add a README ----
echo   推被拒了。我去看看远程那边有什么……
echo.
"%GIT%" fetch origin
set "RCOUNT="
for /f "usebackq delims=" %%c in (`"%GIT%" rev-list --count origin/main 2^>nul`) do set "RCOUNT=%%c"

if "%RCOUNT%"=="1" goto only_one_commit
goto push_failed

:only_one_commit
echo   远程仓库里只有 1 个提交 —— 那是建仓库时自动生成的
echo   （一个 README.md 之类的空壳），没有别的东西。
echo.
echo   本地的项目已经有完整的一次提交了。最干净的做法是
echo   **直接用本地覆盖掉远程那个空壳**：远程就只剩你这个项目。
echo.
set "FORCE="
set /p "FORCE=   覆盖它？（输入 Y 回车 = 覆盖；直接回车 = 不覆盖，我自己处理）："
if /i not "%FORCE%"=="Y" goto push_failed
echo.
echo   覆盖中……
"%GIT%" push --force -u origin main
if errorlevel 1 goto push_failed
echo.
echo   覆盖成功。

:pushed
echo.
echo   成功！代码已经推上去了。
echo.
echo   以后改完还想再推：双击我这个文件，一路回车就行（远程已经配好了）。
echo.
pause
exit /b 0

:push_failed
echo.
echo   没推成功。这些是常见原因：
echo.
echo     1. 仓库地址写错 / 仓库还没建；
echo     2. 远程仓库里已经有别的东西（不止一个空壳提交）——
echo        这种情况别乱覆盖，先把远程地址和内容确认一下再来找我；
echo     3. 登录没通过 —— 重新双击我，在弹窗里认真登一次。
echo.
pause
exit /b 1
