from __future__ import annotations

import json
import logging

from temu_bot.logging_setup import log_event, setup_logging


def test_jsonl_channel_carries_structured_fields(tmp_path):
    log_dir = tmp_path / "logs"
    root = setup_logging(log_dir, level="INFO", console=False, force=True)
    log_event(
        logging.getLogger("temu_bot.test"),
        "写入一行",
        shop="A lovely costume",
        row_key="abc123",
        step="PRECHECK",
        status="ok",
        duration_ms=12,
    )
    root.handlers[0].flush()
    root.handlers[1].flush()

    files = sorted(log_dir.iterdir())
    names = {f.suffix for f in files}
    assert names == {".log", ".jsonl"}

    human_file = next(f for f in files if f.suffix == ".log")
    machine_file = next(f for f in files if f.suffix == ".jsonl")

    payload = json.loads(machine_file.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert payload["msg"] == "写入一行"
    assert payload["level"] == "INFO"
    assert payload["shop"] == "A lovely costume"
    assert payload["row_key"] == "abc123"
    assert payload["step"] == "PRECHECK"
    assert payload["duration_ms"] == 12

    human = human_file.read_text(encoding="utf-8")
    assert "写入一行" in human
    assert "abc123" not in human  # 结构化字段不进人读通道
