"""店铺登录态：从 Playwright 上下文导出，供 httpx 复用。

存到 data/state/session_<店铺>.json（已在 .gitignore 里，里面含 cookie，别外传，别提交）。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from temu_bot.config import Config

log = logging.getLogger(__name__)

SESSION_DIR = Path("state")
BASE_URL = "https://www.dianxiaomi.com"


def _safe(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name).strip("_") or "default"


def session_path(cfg: Config, shop: str) -> Path:
    return cfg.data_dir / SESSION_DIR / f"session_{_safe(shop)}.json"


def _write_json_atomic(path: Path, payload: Any) -> None:
    """原子写：先写临时文件再替换。

    登录进程开着浏览器时会**每 5 秒**存一次登录态，而上架那边每一行都要读这个文件。
    直接 `write_text` 有概率被读到写了一半的 JSON（"登录态文件损坏"），
    所以这里改成 tmp + os.replace——读到的要么是旧的、要么是新的，不会读到半截。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


@dataclass(slots=True)
class ShopSession:
    shop: str
    cookies: list[dict[str, Any]] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)
    exported_at: str = ""
    source: str = ""
    storage_origins: list[dict[str, Any]] = field(default_factory=list)

    # ------------------------------------------------------------ 存取
    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "shop": self.shop,
            "exported_at": self.exported_at or datetime.now().isoformat(timespec="seconds"),
            "source": self.source,
            "headers": self.headers,
            "cookies": self.cookies,
            "storage_origins": self.storage_origins,
        }
        _write_json_atomic(path, payload)
        try:
            path.chmod(0o600)
        except OSError:
            pass  # Windows 上可能不支持，忽略
        log.info("已保存登录态：%s（%d 个 cookie）", path, len(self.cookies))
        return path

    @classmethod
    def load(cls, path: Path) -> "ShopSession":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            shop=data.get("shop", ""),
            cookies=data.get("cookies", []),
            headers=data.get("headers", {}),
            exported_at=data.get("exported_at", ""),
            source=data.get("source", ""),
            storage_origins=data.get("storage_origins", []),
        )

    # ------------------------------------------------------------ 检查
    def cookie_names(self) -> list[str]:
        return sorted({c.get("name", "") for c in self.cookies if c.get("name")})

    def is_empty(self) -> bool:
        return not self.cookies

    def looks_expired(self, now: float | None = None) -> bool:
        """所有 cookie 都过期了就认为登录态失效。"""
        import time

        stamp = now if now is not None else time.time()
        alive = []
        for cookie in self.cookies:
            expires = cookie.get("expires")
            if expires in (None, "", -1):
                alive.append(True)          # session cookie
            else:
                try:
                    alive.append(float(expires) > stamp)
                except (TypeError, ValueError):
                    alive.append(True)
        return self.cookies != [] and not any(alive)


def load_session(cfg: Config, shop: str) -> ShopSession | None:
    path = session_path(cfg, shop)
    if not path.exists():
        return None
    try:
        return ShopSession.load(path)
    except Exception as exc:  # noqa: BLE001
        log.warning("登录态文件损坏（%s）：%s", path, exc)
        return None


def save_session_from_context(cfg: Config, shop: str, context: Any, *, source: str = "playwright") -> ShopSession:
    """从 Playwright BrowserContext 导出 cookie + localStorage 到店铺会话文件。"""
    state = context.storage_state()

    user_agent = ""
    try:
        pages = [p for p in context.pages if not p.is_closed()]
        if pages:
            user_agent = pages[0].evaluate("navigator.userAgent") or ""
    except Exception:  # noqa: BLE001
        pass

    # 只保留与店小秘相关的 origin 的 localStorage
    origins = [
        o for o in state.get("origins", []) if "dianxiaomi.com" in str(o.get("origin", ""))
    ]

    session = ShopSession(
        shop=shop,
        cookies=state.get("cookies", []),
        headers={
            "user-agent": user_agent,
            "referer": BASE_URL + "/",
            "origin": BASE_URL,
            "accept": "application/json, text/plain, */*",
            "x-requested-with": "XMLHttpRequest",
        },
        exported_at=datetime.now().isoformat(timespec="seconds"),
        source=source,
        storage_origins=origins,
    )
    path = session.save(session_path(cfg, shop))

    # localStorage 里可能藏着前端要用的 token，单独存一份备查（不进 git）
    if origins:
        extra = path.with_name(path.stem + "_localstorage.json")
        _write_json_atomic(extra, origins)
        log.info("另存 localStorage：%s", extra)

    return session
