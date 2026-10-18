"""上架成功后，把这次传到店小秘**图片空间**的图删掉。

用户 2026-10-18 要求：图片空间有限，上架成功了就把空间里那几张也清掉。

安全边界（这个功能会真的删东西，所以边界写死）：

1. **只删「这一次」上传的**：拿 `ListingOutcome.uploaded_urls` 里的地址，
   在图片空间里按 **COS 路径**精确匹配。同名但路径不同的老图一律不动。
2. **锁定的不删**：`lockState != 0` 的跳过。
3. **只在发布成功后删**：调用方（`web/jobs.py`）只在 `op=2 立即发布` 且这一行
   成功时才调这里。`op=1 仅保存` / `op=3 移入待发布` 的草稿还引用着这张图，
   删了会把草稿的图弄坏。
4. **删失败不影响上架结果**：商品已经存好了，这里只是顺手省空间，
   出错只记日志。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from temu_bot.config import Config
from temu_bot.dianxiaomi.client import DxmClient

log = logging.getLogger(__name__)


@dataclass(slots=True)
class AlbumCleanResult:
    found: int = 0          # 在空间里按路径找到几张
    deleted: int = 0        # 实际删掉几张
    skipped: int = 0        # 锁定的、跳过的
    error: str = ""         # 出错原因（空 = 一切正常）

    @property
    def ok(self) -> bool:
        return not self.error

    def summary(self) -> str:
        if self.error:
            return f"图片空间清理失败：{self.error}"
        if not self.found:
            return "图片空间里没找到这次上传的图（可能已经被删过）"
        return f"图片空间：找到 {self.found} 张，删除 {self.deleted} 张" + (
            f"，锁定跳过 {self.skipped} 张" if self.skipped else ""
        )


def clean_uploaded_images(
    cfg: Config,
    login: str,
    urls: list[str],
    *,
    client_factory: Callable[..., Any] | None = None,
) -> AlbumCleanResult:
    """把 `urls` 对应的图片从店小秘图片空间删掉。**只按路径精确匹配。**"""
    result = AlbumCleanResult()
    if not urls:
        return result
    if not login:
        result.error = "不知道用哪个登录身份，跳过"
        return result

    factory = client_factory or DxmClient
    try:
        with factory(cfg, login) as client:
            items = client.album_find_by_urls(urls)
            result.found = len(items)

            ids: list[str] = []
            for item in items:
                try:
                    locked = int(item.get("lockState") or 0)
                except (TypeError, ValueError):
                    locked = 0
                if locked:
                    result.skipped += 1
                    continue
                pic_id = str(item.get("idStr") or item.get("id") or "").strip()
                if pic_id:
                    ids.append(pic_id)

            if not ids:
                return result

            resp = client.album_delete(ids)
            code = (resp or {}).get("code")
            if code not in (0, None):
                result.error = f"删除接口返回：{(resp or {}).get('msg') or resp}"
                return result
            result.deleted = len(ids)
    except Exception as exc:  # noqa: BLE001 - 省空间失败不该影响上架结果
        result.error = str(exc)
        log.warning("清理图片空间失败（不影响上架结果）：%s", exc)

    return result
