"""把 temu-bot 打成**绿色免安装目录**（PyInstaller one-dir）。

产物：`dist\\temu-bot\\`，整个文件夹拷到别的 Windows 电脑就能用，
**不需要装 Python**（需要装 Google Chrome，登录用它）。

为什么是 one-dir 而不是单文件 exe：
- 单文件每次启动都要把自己解到临时目录，慢；one-dir 直接跑。
- Playwright 带一个 node 驱动器，单文件模式下这类"自带二进制"的库容易出怪问题。

用法：
    uv sync --group packaging          # 装一次 PyInstaller
    .venv\\Scripts\\python.exe tools\\build_app.py

打完记得**在别的目录里试跑一次**（别在本项目目录里跑，那会用到源码）：
    cd dist\\temu-bot
    启动 temu-bot.bat
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
APP_NAME = "temu-bot"
OUT_DIR = DIST / APP_NAME

# 前端静态文件（FastAPI 直接按路径读，必须跟着走）
STATIC_SRC = ROOT / "src" / "temu_bot" / "web" / "static"
STATIC_DEST = "temu_bot/web/static"

LAUNCHER = """@echo off
chcp 65001 >nul
cd /d "%~dp0"
title temu-bot 控制台
set PYTHONIOENCODING=utf-8

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

"%~dp0temu-bot.exe" serve --port 8765

echo.
echo   服务已停止。
pause
"""

README = """temu-bot —— 绿色免安装版
================================================

怎么用
--------
双击「启动 temu-bot.bat」，浏览器会自动打开控制台。
关掉那个黑窗口就是关掉程序。

详细用法看同目录的「使用手册.txt」—— 那里什么都写了。

首次使用
--------
1. 装 Google Chrome（登录店小秘要用系统 Chrome；装过了就跳过）。
2. 双击「1-登录店小秘.bat」，在弹出的窗口里登录一次。之后长期复用。
3. 回到控制台，在「设置 -> 路径」里把「商品信息表」改成这台电脑上的表路径。

目录里都有什么
--------------
  temu-bot.exe          程序本体（命令行入口，一般不用直接点它）
  启动 temu-bot.bat     主入口 —— 双击这个
  1-登录店小秘.bat      登录店小秘（要换账号时也用它）
  使用手册.txt          完整说明书
  config\\config.yaml    配置（路径、速度、店铺提示等，可直接用记事本改）
  data\\                 日志、备份、ID 台账、登录态（不要删）
  work\\                 出图/传图的中间产物，可以随时删
  profiles\\             浏览器配置（登录态）
  _internal\\            PyInstaller 带的运行时，别动

注意
----
- 这一整套是你自己的店铺数据，别把 data\\ 或 profiles\\ 发给别人。
- 想升级：把新版整个文件夹覆盖过来，保留 config\\、data\\、profiles\\ 即可。
"""


def _run(cmd: list[str]) -> None:
    print("$", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(ROOT))
    if proc.returncode != 0:
        raise SystemExit(f"命令失败（退出码 {proc.returncode}）")


# 重新打包时**必须保住**的用户数据：配置、日志/台账/登录态、浏览器 profile。
# 直接 rmtree(OUT_DIR) 会把店铺登录态一起删掉——那是重登一次都补不回来的东西。
PRESERVE = ("config", "data", "profiles")


def _stash_user_data(out_dir: Path) -> Path | None:
    """打包前把 `config/`、`data/`、`profiles/` 挪到旁边，打完再放回去。"""
    if not out_dir.is_dir():
        return None
    if not any((out_dir / name).exists() for name in PRESERVE):
        return None

    stash = out_dir.parent / (out_dir.name + ".keep")
    if stash.exists():
        shutil.rmtree(stash)
    stash.mkdir(parents=True)
    for name in PRESERVE:
        src = out_dir / name
        if src.exists():
            shutil.move(str(src), str(stash / name))
            print(f"  先保留 {name}\\（打包完再放回去，别弄丢登录态）")
    return stash


def _restore_user_data(out_dir: Path, stash: Path | None) -> None:
    """把挪开的用户数据放回去。

    `config/` 用**旧的覆盖新的**：你本机那份 config.yaml 里的路径才是对的；
    但新版本多出来的文件（比如 `selectors/`）会保留下来。
    `data/`、`profiles/` 原样搬回。
    """
    if stash is None or not stash.exists():
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    old_cfg = stash / "config"
    if old_cfg.is_dir():
        (out_dir / "config").mkdir(parents=True, exist_ok=True)
        shutil.copytree(old_cfg, out_dir / "config", dirs_exist_ok=True)
    for name in ("data", "profiles"):
        src = stash / name
        if src.exists():
            dst = out_dir / name
            if dst.exists():
                shutil.rmtree(dst)
            shutil.move(str(src), str(dst))
    shutil.rmtree(stash, ignore_errors=True)
    print("  已把 config\\、data\\、profiles\\ 放回新包（登录态没丢）")


def main() -> int:
    if not (ROOT / ".venv" / "Scripts" / "python.exe").exists():
        raise SystemExit("找不到 .venv，先跑一次 uv sync")
    if not STATIC_SRC.is_dir():
        raise SystemExit(f"找不到前端静态文件：{STATIC_SRC}")

    py = str(ROOT / ".venv" / "Scripts" / "python.exe")

    # 入口脚本：双击 exe 时不带参数会打印 CLI 帮助；真正的主入口是那个 .bat
    entry = ROOT / "packaging" / "entry.py"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(
        '"""打包入口：直接转发给 CLI。"""\n'
        "from temu_bot.cli import main\n\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
        encoding="utf-8",
    )

    print("== 清掉上次的产物 ==")
    # 先把用户数据挪开：dist\temu-bot\ 里可能已经有 data\（台账/登录态）和 profiles\，
    # 直接 rmtree 会一起删掉——那是重登一次都补不回来的东西。
    stash = _stash_user_data(OUT_DIR)
    for path in (ROOT / "build", OUT_DIR):
        if not path.exists():
            continue
        try:
            shutil.rmtree(path)
        except OSError as exc:
            # 文件夹被占着（记事本开着说明/Excel 开着配置/资源管理器停在里面/程序还在跑）。
            # 先把用户数据放回去，再用一句人话告诉用户关什么。
            _restore_user_data(OUT_DIR, stash)
            raise SystemExit(
                f"删不掉 {path}：{exc}\n"
                "  多半是还有东西占着这个文件夹。请关掉：\n"
                "    · 用记事本/Excel 打开着的 使用说明.txt / 使用手册.txt / config.yaml\n"
                "    · 停在这个文件夹里的资源管理器窗口\n"
                "    · 还在跑的 temu-bot.exe，或它拉起来的 Chrome 窗口\n"
                "  然后再跑一次。你的 config\\、data\\、profiles\\ 已经放回原位了。"
            ) from exc
        print(f"  删除 {path}")

    print("== 跑 PyInstaller ==")
    try:
        _run([
        py, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",                       # 绿色目录，不要单文件
        "--console",                      # 控制台程序：那个黑窗口就是主界面
        "--name", APP_NAME,
        "--distpath", str(DIST),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"),
        "--paths", str(ROOT / "src"),
        # 前端静态文件
        "--add-data", f"{STATIC_SRC}{';' if sys.platform == 'win32' else ':'}{STATIC_DEST}",
        # Playwright 自带 node 驱动器，PyInstaller 扫不到，得整包收
        "--collect-all", "playwright",
        # uvicorn 的协议是运行时动态导入的
        "--collect-submodules", "uvicorn",
        str(entry),
        ])
    except BaseException:
        # 打包失败了也要把用户数据放回去（产物可能不完整，但登录态/台账不能丢）
        _restore_user_data(OUT_DIR, stash)
        raise

    print("== 补上配置、启动器、说明 ==")
    (OUT_DIR / "config").mkdir(parents=True, exist_ok=True)
    src_cfg = ROOT / "config" / "config.yaml"
    shutil.copy2(src_cfg, OUT_DIR / "config" / "config.yaml")
    print(f"  复制 config/config.yaml（照抄当前这份，新电脑上按需改路径）")

    # 选择器/入口地址也得跟着走：登录窗口默认开哪里就写在里面（urls.start）。
    # 少这一份时程序会回退到内置的店小秘首页，但带上它更稳、也方便以后改地址。
    selectors_src = ROOT / "config" / "selectors"
    if selectors_src.is_dir():
        shutil.copytree(selectors_src, OUT_DIR / "config" / "selectors", dirs_exist_ok=True)
        print("  复制 config/selectors/")
    else:
        print(f"  [警告] 找不到 {selectors_src}，包里就没有选择器配置（不影响登录：会回退到店小秘首页）")

    launcher = OUT_DIR / "启动 temu-bot.bat"
    launcher.write_bytes(LAUNCHER.replace("\n", "\r\n").encode("utf-8"))
    print(f"  写入 {launcher.name}")

    # 单独一个登录入口：第一次用、或要换账号时用得到
    login = OUT_DIR / "1-登录店小秘.bat"
    login.write_bytes(
        (
            "@echo off\r\n"
            "chcp 65001 >nul\r\n"
            'cd /d "%~dp0"\r\n'
            "set PYTHONIOENCODING=utf-8\r\n"
            "echo.\r\n"
            "echo   登录店小秘：浏览器窗口里登录完，把窗口关掉就算完事。\r\n"
            "echo   登录身份留空 = 按表格里的店铺名自动认。\r\n"
            "echo.\r\n"
            'set /p SHOP=登录身份（留空 = 自动认）: \r\n'
            'if "%SHOP%"=="" (\r\n'
            '  "%~dp0temu-bot.exe" login --until-closed\r\n'
            ") else (\r\n"
            '  "%~dp0temu-bot.exe" login --shop "%SHOP%" --until-closed\r\n'
            ")\r\n"
            "pause\r\n"
        ).encode("utf-8")
    )
    print(f"  写入 {login.name}")

    (OUT_DIR / "使用说明.txt").write_bytes(README.replace("\n", "\r\n").encode("utf-8"))
    print("  写入 使用说明.txt")

    # 完整手册就是个纯文本，直接照抄过去（不生成 HTML、不写转换脚本）
    manual_src = ROOT / "docs" / "使用手册.txt"
    if manual_src.is_file():
        shutil.copy2(manual_src, OUT_DIR / "使用手册.txt")
        print("  复制 docs/使用手册.txt")
    else:
        print(f"  [警告] 找不到 {manual_src}，包里就没有手册")

    total = sum(f.stat().st_size for f in OUT_DIR.rglob("*") if f.is_file())
    _restore_user_data(OUT_DIR, stash)
    print()
    print(f"== 完成：{OUT_DIR} ==")
    print(f"   共 {total / 1024 / 1024:.0f} MB")
    print("   下一步：把这个文件夹整个拷到别的电脑，双击「启动 temu-bot.bat」")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
