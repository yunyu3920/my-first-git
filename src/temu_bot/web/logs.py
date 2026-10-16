"""日志环形缓冲：把跑过程中的日志留在内存里，供网页实时取用。

`logging_setup` 只往文件写（`data/logs/<date>.log` + `.jsonl`），没有"取最近 N 条"的能力。
这里加一个 handler 把记录同时存进定长队列，并给每条打一个**只增不减的 seq**——
网页拿 `after=<seq>` 就能只取新增的部分，SSE 和轮询两种方式都能用。

为什么不用 `threading.Condition` 阻塞等待：那会卡住 uvicorn 的 event loop。
调用方（`app.py`）改成 `asyncio.sleep` 轮询，量级完全够，也不会漏线程。
"""

from __future__ import annotations

import collections
import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any

# 缓冲上限：本地控制台只需要"最近发生了什么"
DEFAULT_CAPACITY = 2000

# 这些 logger 只会刷屏，不进网页日志面板
NOISY_LOGGERS = ("uvicorn.access", "httpx", "httpcore", "asyncio", "filelock")


@dataclass(frozen=True, slots=True)
class LogEntry:
    seq: int
    ts: str
    level: str
    logger: str
    msg: str
    fields: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "seq": self.seq,
            "ts": self.ts,
            "level": self.level,
            "logger": self.logger,
            "msg": self.msg,
        }
        if self.fields:
            out["fields"] = self.fields
        return out


class RingBuffer:
    """定长日志队列。加锁是必须的：写日志的是工作线程，读的是 web 线程。"""

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        self._items: collections.deque[LogEntry] = collections.deque(maxlen=capacity)
        self._seq = 0
        self._lock = threading.Lock()

    def add(
        self,
        *,
        level: str,
        logger: str,
        msg: str,
        ts: str | None = None,
        fields: dict[str, Any] | None = None,
    ) -> LogEntry:
        """分配序号并入队——**必须在同一把锁里做完**。

        分成"先取号、再入队"两步的话，两个线程可能 A 取到 5、B 取到 6，
        但 B 先入队，deque 里就成了 6、5，增量读取的顺序就乱了。
        """
        with self._lock:
            self._seq += 1
            entry = LogEntry(
                seq=self._seq,
                ts=ts or datetime.now().strftime("%H:%M:%S"),
                level=level,
                logger=logger,
                msg=msg,
                fields=fields,
            )
            self._items.append(entry)
            return entry

    def since(self, after: int = 0, limit: int | None = None) -> list[LogEntry]:
        """取 seq > after 的记录（早于缓冲区的就取不到了，调用方按当前 seq 对齐）。

        遍历也要持锁：`deque` 在遍历途中被别的线程 `append` 会抛
        `RuntimeError: deque mutated during iteration`。
        """
        with self._lock:
            items = [e for e in self._items if e.seq > after]
        return items[:limit] if limit else items

    def latest_seq(self) -> int:
        with self._lock:
            return self._seq

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


class RingBufferHandler(logging.Handler):
    """把日志记录灌进环形缓冲。"""

    def __init__(self, buffer: RingBuffer, level: int = logging.INFO) -> None:
        super().__init__(level=level)
        self.buffer = buffer

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith(NOISY_LOGGERS):
            return
        try:
            fields = getattr(record, "fields", None)
            self.buffer.add(
                level=record.levelname,
                logger=record.name,
                msg=record.getMessage(),
                ts=datetime.fromtimestamp(record.created).strftime("%H:%M:%S"),
                fields=fields if isinstance(fields, dict) else None,
            )
        except Exception:  # noqa: BLE001
            # 日志处理器自己出错绝不能往外抛（logging 文档明确要求）
            self.handleError(record)


class LogBuffer:
    """环形缓冲 + handler 的装配，保证重复调用不会装两遍。"""

    HANDLER_NAME = "temu-bot-ring"

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        self.buffer = RingBuffer(capacity=capacity)
        self.handler = RingBufferHandler(self.buffer)

    def attach(self, logger: logging.Logger | None = None) -> RingBufferHandler:
        root = logger or logging.getLogger()
        for existing in list(root.handlers):
            if getattr(existing, "name", "") == self.HANDLER_NAME:
                root.removeHandler(existing)
        self.handler.name = self.HANDLER_NAME
        self.handler.setLevel(logging.INFO)
        root.addHandler(self.handler)
        return self.handler

    def detach(self, logger: logging.Logger | None = None) -> None:
        root = logger or logging.getLogger()
        if self.handler in root.handlers:
            root.removeHandler(self.handler)
