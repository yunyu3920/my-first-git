"""日志环形缓冲测试：顺序、上限、并发不炸。"""

from __future__ import annotations

import logging
import threading

from temu_bot.web.logs import LogBuffer, RingBuffer, RingBufferHandler


def test_seq_is_monotonic_and_since_filters():
    buf = RingBuffer()
    for i in range(5):
        buf.add(level="INFO", logger="t", msg=f"m{i}")

    assert [e.seq for e in buf.since(0)] == [1, 2, 3, 4, 5]
    assert [e.msg for e in buf.since(3)] == ["m3", "m4"]
    assert buf.latest_seq() == 5


def test_capacity_is_bounded_and_since_skips_dropped():
    buf = RingBuffer(capacity=3)
    for i in range(6):
        buf.add(level="INFO", logger="t", msg=f"m{i}")

    assert len(buf) == 3
    assert [e.msg for e in buf.since(0)] == ["m3", "m4", "m5"]


def test_limit_caps_the_batch():
    buf = RingBuffer()
    for i in range(10):
        buf.add(level="INFO", logger="t", msg=f"m{i}")
    assert len(buf.since(0, limit=4)) == 4


def test_since_is_safe_while_another_thread_appends():
    """遍历时另一个线程 append 会抛 deque mutated during iteration——锁必须兜住。"""
    buf = RingBuffer(capacity=5000)
    stop = threading.Event()
    errors: list[BaseException] = []

    def writer():
        i = 0
        while not stop.is_set():
            buf.add(level="INFO", logger="t", msg=f"w{i}")
            i += 1

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    try:
        for _ in range(300):
            try:
                buf.since(0)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
                break
    finally:
        stop.set()
        thread.join(timeout=5)

    assert not errors, errors
    assert not thread.is_alive()


def test_handler_ignores_noisy_loggers():
    buf = RingBuffer()
    handler = RingBufferHandler(buf)
    logger = logging.getLogger("test.web.logs.noisy")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        logging.getLogger("httpx").warning("这不该进面板")
        logger.info("这条要进")
    finally:
        logger.removeHandler(handler)

    msgs = [e.msg for e in buf.since(0)]
    assert "这条要进" in msgs
    assert "这不该进面板" not in msgs


def test_handler_carries_structured_fields():
    buf = RingBuffer()
    handler = RingBufferHandler(buf)
    logger = logging.getLogger("test.web.logs.fields")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        logger.info("带字段", extra={"fields": {"row": 7, "step": "MEDIA"}})
    finally:
        logger.removeHandler(handler)

    entry = buf.since(0)[0]
    assert entry.fields == {"row": 7, "step": "MEDIA"}
    assert entry.as_dict()["fields"]["step"] == "MEDIA"


def test_attach_is_idempotent():
    """重复装配不能出现两个 handler，否则网页上每条日志会重复一次。"""
    root = logging.getLogger()
    before = list(root.handlers)
    logs = LogBuffer()
    try:
        logs.attach()
        logs.attach()
        installed = [h for h in root.handlers if getattr(h, "name", "") == LogBuffer.HANDLER_NAME]
        assert len(installed) == 1
    finally:
        logs.detach()
        assert list(root.handlers) == before
