"""看一眼 state 下的日志文件（显式 UTF-8，别用 PowerShell 的默认编码）。"""

from __future__ import annotations

import json
from pathlib import Path

for name in ("runs.jsonl", "live_runs.jsonl"):
    path = Path("data/state") / name
    if not path.exists():
        print(f"{name}: 不存在")
        continue
    lines = path.read_text(encoding="utf-8").splitlines()
    print(f"--- {name}（{len(lines)} 行）")
    for line in lines[-2:]:
        item = json.loads(line)
        item.pop("steps", None)
        print("   ", json.dumps(item, ensure_ascii=False))
