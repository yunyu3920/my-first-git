"""日志：人读 .log + 机读 .jsonl 双通道，落在 data\\logs\\<date>.*；另有 rich 控制台。

结构化字段通过 extra={"fields": {...}} 传入，会被 jsonl 通道合并进每一行，
便于后续用 execute report / 控制台做按 (shop, row_key, step) 的筛选统计。
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any

FIELDS_ATTR = "fields"
_configured = False


class JsonlFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, FIELDS_ATTR, None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["err"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def log_event(logger: logging.Logger, msg: str, level: int = logging.INFO, **fields: Any) -> None:
    """带结构化字段的日志。字段会进 jsonl，人读通道只显示消息。"""
    logger.log(level, msg, extra={FIELDS_ATTR: fields} if fields else None)


def setup_logging(
    log_dir: Path,
    *,
    level: str = "INFO",
    console: bool = True,
    force: bool = False,
) -> logging.Logger:
    """初始化根 logger。重复调用默认幂等（除非 force=True）。"""
    global _configured
    root = logging.getLogger()
    if _configured and not force:
        return root

    for handler in list(root.handlers):
        root.removeHandler(handler)

    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    lvl = getattr(logging, str(level).upper(), logging.INFO)

    human = logging.FileHandler(log_dir / f"{stamp}.log", encoding="utf-8")
    human.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    )

    machine = logging.FileHandler(log_dir / f"{stamp}.jsonl", encoding="utf-8")
    machine.setFormatter(JsonlFormatter())

    root.addHandler(human)
    root.addHandler(machine)

    if console:
        try:
            from rich.logging import RichHandler

            rich_handler = RichHandler(rich_tracebacks=True, show_path=False, markup=False)
            rich_handler.setFormatter(logging.Formatter("%(message)s"))
            root.addHandler(rich_handler)
        except Exception:  # rich 缺失时退回普通 stderr
            stream = logging.StreamHandler(sys.stderr)
            stream.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
            root.addHandler(stream)

    root.setLevel(lvl)
    # 第三方库降噪
    for noisy in ("httpx", "httpcore", "urllib3", "asyncio", "filelock", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True
    return root
