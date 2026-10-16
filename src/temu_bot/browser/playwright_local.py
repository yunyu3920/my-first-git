"""Playwright 自带 Chromium + persistent user_data_dir 的实现。

登录态就在 user_data_dir 里（每店铺一个），所以只需人工登录一次。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout

from temu_bot.config import Config, ShopCfg
from temu_bot.browser.base import BrowserError

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_MS = 60_000


class PlaywrightLocalSession:
    def __init__(self, cfg: Config, shop: ShopCfg, *, start_url: str | None = None) -> None:
        self.cfg = cfg
        self.shop = shop.name
        self._start_url = start_url
        self._pw = None
        self._ctx = None
        self._page = None
        self._lock: FileLock | None = None

    # ------------------------------------------------------------ 生命周期
    @property
    def profile_dir(self) -> Path:
        if self.shop_cfg.profile_dir is None:
            raise BrowserError(f"店铺「{self.shop}」没配 profile_dir，无法启动浏览器")
        return Path(self.shop_cfg.profile_dir)

    @property
    def shop_cfg(self) -> ShopCfg:
        return self.cfg.shop_or_default(self.shop)

    def __enter__(self) -> "PlaywrightLocalSession":
        self._open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _open(self) -> None:
        from playwright.sync_api import sync_playwright

        profile = self.profile_dir
        profile.mkdir(parents=True, exist_ok=True)

        # 同一个 profile 不许被两个进程同时开（Chromium profile 锁）
        lock_file = profile.parent / f".{profile.name}.lock"
        self._lock = FileLock(lock_file, timeout=5)
        try:
            self._lock.acquire()
        except Timeout as exc:
            raise BrowserError(
                f"店铺「{self.shop}」的浏览器 profile 正被另一个进程占用：{profile}"
            ) from exc

        browser_cfg = self.cfg.browser
        log.info("启动浏览器：shop=%s profile=%s headless=%s", self.shop, profile, browser_cfg.headless)

        self._pw = sync_playwright().start()
        kwargs: dict[str, Any] = {
            "user_data_dir": str(profile),
            "headless": browser_cfg.headless,
            "args": list(browser_cfg.args),
            "viewport": {"width": browser_cfg.viewport.width, "height": browser_cfg.viewport.height},
            "slow_mo": browser_cfg.slow_mo_ms,
            "accept_downloads": True,
        }
        # executable_path 优先（用户指定的 chrome.exe）；否则用 channel 让 Playwright 自己找
        if browser_cfg.executable_path:
            exe = Path(browser_cfg.executable_path)
            if not exe.exists():
                raise BrowserError(f"配置里的浏览器路径不存在：{exe}")
            kwargs["executable_path"] = str(exe)
            log.info("使用指定浏览器：%s", exe)
        elif browser_cfg.channel:
            kwargs["channel"] = browser_cfg.channel
            log.info("使用 channel=%s", browser_cfg.channel)

        try:
            self._ctx = self._pw.chromium.launch_persistent_context(**kwargs)
        except Exception as exc:  # noqa: BLE001 - 统一成 BrowserError，附上可操作建议
            self._cleanup_after_failure()
            hint = ""
            if "Executable doesn't exist" in str(exc):
                hint = "（跑 `.venv\\Scripts\\playwright.exe install chromium`，或把 browser.channel 设为 chrome）"
            raise BrowserError(f"浏览器启动失败：{exc}{hint}") from exc

        self._ctx.set_default_timeout(DEFAULT_TIMEOUT_MS)
        self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()

        if self._start_url:
            self.open(self._start_url)

    def _cleanup_after_failure(self) -> None:
        try:
            if self._pw is not None:
                self._pw.stop()
        finally:
            self._pw = None
            if self._lock is not None:
                try:
                    self._lock.release()
                except Exception:  # noqa: BLE001
                    pass
                self._lock = None

    def is_open(self) -> bool:
        """浏览器窗口还开着吗（还有没有活着的标签页）。

        给"关掉窗口=操作完了"这种流程用——控制台起的登录进程靠它判断什么时候收工。
        """
        ctx = self._ctx
        if ctx is None:
            return False
        try:
            return any(not page.is_closed() for page in ctx.pages)
        except Exception:  # noqa: BLE001 - 上下文没了就当已关
            return False

    def close(self) -> None:
        try:
            if self._ctx is not None:
                self._ctx.close()
        except Exception as exc:  # noqa: BLE001
            log.warning("关闭浏览器上下文时报错：%s", exc)
        finally:
            self._ctx = None
            self._page = None
            if self._pw is not None:
                try:
                    self._pw.stop()
                except Exception as exc:  # noqa: BLE001
                    log.warning("停止 playwright 时报错：%s", exc)
                self._pw = None
            if self._lock is not None:
                try:
                    self._lock.release()
                except Exception:  # noqa: BLE001
                    pass
                self._lock = None

    # ------------------------------------------------------------ 页面
    def page(self) -> Any:
        if self._ctx is None:
            raise BrowserError("浏览器还没启动（用 with 语句包住）")
        if self._page is None or self._page.is_closed():
            pages = [p for p in self._ctx.pages if not p.is_closed()]
            self._page = pages[-1] if pages else self._ctx.new_page()
        return self._page

    def pages(self) -> list[Any]:
        return [p for p in (self._ctx.pages if self._ctx else []) if not p.is_closed()]

    def context(self) -> Any:
        """暴露 Playwright 上下文（导出登录态、挂录制器要用）。"""
        if self._ctx is None:
            raise BrowserError("浏览器还没启动（用 with 语句包住）")
        return self._ctx

    def open(self, url: str, *, wait_until: str = "domcontentloaded", timeout_ms: int = DEFAULT_TIMEOUT_MS) -> Any:
        page = self.page()
        log.info("打开 %s", url)
        page.goto(url, wait_until=wait_until, timeout=timeout_ms)
        self._bring_to_front(page)
        return page

    @staticmethod
    def _bring_to_front(page: Any) -> None:
        try:
            page.bring_to_front()
        except Exception:  # noqa: BLE001 - headless 下会失败，忽略
            pass

    def current_url(self) -> str:
        try:
            return self.page().url
        except Exception:  # noqa: BLE001
            return ""

    def is_logged_in(self) -> bool:
        from temu_bot.browser.inspect import looks_logged_in

        return looks_logged_in(self.cfg, self.current_url(), self.page())


class PlaywrightLocalProvider:
    name = "playwright_local"

    def launch(self, cfg: Config, shop: ShopCfg, *, start_url: str | None = None) -> PlaywrightLocalSession:
        # 没配 profile_dir 的店铺按名字派生一个
        if shop.profile_dir is None:
            safe = "".join(ch if ch.isalnum() else "_" for ch in shop.name).strip("_") or "default"
            shop = shop.model_copy(update={"profile_dir": cfg.root / "profiles" / safe})
        return PlaywrightLocalSession(cfg, shop, start_url=start_url)
