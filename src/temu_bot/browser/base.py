"""浏览器适配层：统一接口，三种实现可插拔。

- playwright_local：Playwright 自带 Chromium + 独立 user_data_dir（**本期用这个**）
- system_chrome    ：系统 Chrome/Edge + 独立 user_data_dir（自带浏览器不可用时兜底）
- zhanfu           ：站斧指纹浏览器（走它本地 API 拿 CDP，后续再接）

约定：一个店铺一个 profile 目录 = 一份登录态。同一 profile 绝不能被两个进程同时打开
（Chromium 会因 profile 锁起不来），所以 provider 自己负责加文件锁。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from temu_bot.config import Config, ShopCfg


@runtime_checkable
class BrowserSession(Protocol):
    """一次浏览器会话。用 with 语句管理生命周期。"""

    shop: str

    def page(self) -> Any:
        """当前活动页（没有就新建一个）。"""
        ...

    def open(self, url: str, *, wait_until: str = "domcontentloaded", timeout_ms: int = 60_000) -> Any:
        """在活动页打开 URL。"""
        ...

    def is_logged_in(self) -> bool:
        """当前是否处于登录后的状态。"""
        ...

    def close(self) -> None:
        ...

    def __enter__(self) -> "BrowserSession":
        ...

    def __exit__(self, *exc: object) -> None:
        ...


@runtime_checkable
class BrowserProvider(Protocol):
    name: str

    def launch(self, cfg: Config, shop: ShopCfg, *, start_url: str | None = None) -> BrowserSession:
        ...


class BrowserError(RuntimeError):
    """浏览器起不来/被占用/超时等。"""
