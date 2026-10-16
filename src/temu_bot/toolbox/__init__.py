"""工具箱：把《图片批处理工具箱》的四个能力**原生**搬进 temu_bot。

| 模块 | 干什么 |
|---|---|
| `rename`  | SKU 重命名 —— 11 位唯一 ID，4 种模式 |
| `listing` | 导出图片清单 —— CSV（GBK，逐字节兼容老格式）+ XLSX |
| `convert` | 格式转换 —— png / jpg / webp / avif（Pillow，不需要 Node） |
| `merge`   | Excel 合并 —— 目录里所有工作簿纵向拼成一个 |
| `ids`     | 11 位 ID 台账（并进项目了，不再依赖桌面那个文件夹） |
| `bridge`  | 旧路子：子进程调桌面工具的 Core.ps1。**只剩 doctor 自检在用它** |

每个工具都是 `plan(...)`（干跑预览，**一个字节都不写**）+ `run(...)`（真干）配对。
"""

from __future__ import annotations

from temu_bot.config import Config
from temu_bot.toolbox import convert, ids, listing, merge, rename
from temu_bot.toolbox.bridge import ToolboxError, probe, run_ps
from temu_bot.toolbox.result import (
    FAILED,
    OK,
    SKIPPED,
    STATUS_LABELS,
    WOULD,
    ToolItem,
    ToolReport,
)

__all__ = [
    "FAILED",
    "OK",
    "SKIPPED",
    "STATUS_LABELS",
    "WOULD",
    "ToolItem",
    "ToolReport",
    "ToolboxError",
    "convert",
    "ids",
    "listing",
    "merge",
    "probe",
    "rename",
    "run_ps",
    "selfcheck",
]


def selfcheck(cfg: Config) -> dict[str, object]:
    """工具箱自检：台账在哪、有多少个号、Pillow 支不支持那几个格式。"""
    from PIL import Image, features

    supports = {name: bool(features.check(name)) for name in ("avif", "webp")}
    heic = ".heic" in Image.registered_extensions()
    return {
        "ledger": ids.describe(cfg),
        "pillow": {
            "version": getattr(__import__("PIL"), "__version__", ""),
            "avif": supports["avif"],
            "webp": supports["webp"],
            "heic": heic,
        },
        "targets": list(convert.TARGET_FORMATS),
        "modes": list(rename.MODES),
        "ok": supports["avif"] and supports["webp"],
    }
