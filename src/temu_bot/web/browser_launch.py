"""从控制台**另起一个进程**打开店小秘，让你确认店铺 / 登录 / 切换店铺。

用户 2026-10-16 要求：上架之前程序自动把网站打开，好让人自己①确认店铺信息对不对，
②需要的话当场登录或切换店铺。

**为什么另起进程，而不是在服务里直接用 Playwright**：
Playwright 的同步 API 有线程亲和性，在 FastAPI 的线程里长时间握着浏览器会话
风险太大（服务一重启/刷页面就可能留下一个死掉的浏览器）。另起一个进程 = 完全隔离，
它崩了也不影响控制台，而且它自己的锁（`profiles/.<名字>.lock`）还能防"同一个
profile 被开两次"。

另起的进程走的是 `temu-bot login --shop <身份> --until-closed`：
浏览器窗口开着的时候每隔几秒存一次登录态，**你把窗口关掉**它就收工。
（不能等关了再存——cookie 那时已经读不到了。）
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# 同一个登录身份同时只允许开一个（浏览器 profile 本身也有锁，这里提前拦一下，
# 好给出人话提示而不是一句 Playwright 报错）
_OPEN_LOCK = threading.Lock()
_OPEN: dict[str, "Launch"] = {}

# 打开后多久算"超时自动收工"（跟 CLI 的 --hold-minutes 一致）
DEFAULT_HOLD_MINUTES = 30


@dataclass
class Launch:
    login: str
    process: subprocess.Popen
    started_at: float = field(default_factory=time.monotonic)

    @property
    def running(self) -> bool:
        return self.process.poll() is None

    @property
    def seconds(self) -> int:
        return int(time.monotonic() - self.started_at)

    def as_dict(self) -> dict:
        return {
            "login": self.login,
            "running": self.running,
            "seconds": self.seconds,
            "exit_code": self.process.poll(),
        }


class AlreadyOpen(RuntimeError):
    """这个身份的浏览器窗口已经开着，别叠窗。"""


def _cli_command() -> list[str]:
    """怎么调起我们自己这个 CLI（源码跑 / 打包成 exe 两种情况）。"""
    if getattr(sys, "frozen", False):          # PyInstaller：exe 就是入口
        return [sys.executable]
    exe = Path(sys.executable).with_name("temu-bot.exe")
    if exe.exists():
        return [str(exe)]
    return [sys.executable, "-m", "temu_bot.cli"]


def open_site(login: str, *, url: str | None = None, hold_minutes: int = DEFAULT_HOLD_MINUTES) -> dict:
    """打开浏览器逛店小秘（登录/切店铺），**立刻返回**，不等浏览器关掉。"""
    login = (login or "").strip()
    if not login:
        raise ValueError("没指定登录身份（profiles/<名字>）")

    with _OPEN_LOCK:
        existing = _OPEN.get(login)
        if existing is not None and existing.running:
            raise AlreadyOpen(
                f"「{login}」的浏览器窗口已经开着了（已 {existing.seconds} 秒）——"
                f"先在那边操作，关掉窗口再点。"
            )

        cmd = [
            *_cli_command(),
            "login",
            "--shop", login,
            "--until-closed",
            "--hold-minutes", str(hold_minutes),
        ]
        if url:
            cmd += ["--url", url]

        log.info("打开店小秘：%s", " ".join(cmd))
        # CREATE_NO_WINDOW：别弹一个黑窗口；浏览器窗口该显示还是会显示
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen(  # noqa: S603 - 参数是拼好的列表，没有 shell
            cmd,
            creationflags=creationflags,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        launch = Launch(login=login, process=process)
        _OPEN[login] = launch

    return {
        **launch.as_dict(),
        "message": (
            f"已打开浏览器（登录身份「{login}」）。请在窗口里确认店铺信息；"
            f"需要的话当场登录或切换店铺。**弄完后把浏览器窗口关掉**，"
            f"登录态会自动保存（最长等 {hold_minutes} 分钟）。"
        ),
    }


def pick_login_for_open(cfg: Any, shop_name: str) -> tuple[str, str]:
    """**要打开哪个身份的浏览器？**返回 `(登录身份, 说明)`。

    用户不手选身份了，所以这里自己推断：

      1. 表里这家店在某个身份的账号里 → 开那个（最准）
      2. 否则看配置里哪个身份的店名提示对得上 → 开它（配置只是提示，但这时候能帮上忙）
      3. 再否则：只有一个身份就开它；有多个就开第一个

    说明文字会一起返回，好让用户知道"给你开的是哪个、为什么"。
    """
    from temu_bot.dianxiaomi.client import find_login_for_shop

    lookup = find_login_for_shop(cfg, shop_name)
    if lookup.ok:
        return (
            lookup.login,
            f"表里「{shop_name}」在「{lookup.login}」的账号里，打开的是这个身份。",
        )

    infos = cfg.logins()
    if not infos:
        raise ValueError("一个登录身份都没有——先用 1-登录店小秘.bat 登录一次")

    hint = next((i for i in infos if i.shop_hint and i.shop_hint.strip() == (shop_name or "").strip()), None)
    if hint is not None:
        return (
            hint.login,
            f"表里「{shop_name}」现在哪个账号里都没找到；配置里记着它属于「{hint.login}」，"
            f"先打开这个身份——进去看看店铺/登录对不对。",
        )

    first = infos[0]
    why = (
        f"表里「{shop_name}」现在哪个账号里都没找到"
        + (f"（可用身份：{'、'.join(i.login for i in infos)}）" if len(infos) > 1 else "")
        + f"，先打开「{first.login}」——进去确认店铺 / 把该登的账号登好。"
    )
    return first.login, why


def open_for_shop(cfg: Any, shop_name: str, *, url: str | None = None,
                  hold_minutes: int = DEFAULT_HOLD_MINUTES) -> dict:
    """按**表格里的店铺名**决定开哪个浏览器（界面不再让人手选身份）。"""
    login, why = pick_login_for_open(cfg, shop_name)
    result = open_site(login, url=url, hold_minutes=hold_minutes)
    return {**result, "login": login, "why": why, "message": f"{why}\n{result['message']}"}


def status(login: str | None = None) -> dict:
    """看看哪个窗口还开着。"""
    with _OPEN_LOCK:
        open_items = {
            key: item.as_dict() for key, item in _OPEN.items() if item.running
        }
    if login:
        return {"login": login, **open_items.get(login, {"running": False})}
    return {"open": open_items, "any_running": bool(open_items)}


def close(login: str) -> dict:
    """把某个身份的窗口关掉（一般不用——用户自己关窗口就行）。"""
    with _OPEN_LOCK:
        item = _OPEN.get(login)
    if item is None or not item.running:
        return {"ok": False, "message": f"「{login}」的窗口没开着"}
    item.process.terminate()
    return {"ok": True, "message": f"已请求关闭「{login}」的窗口"}
