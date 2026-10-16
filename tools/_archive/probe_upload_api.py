"""探 /api/cos/getSign.json 要哪些参数（只读：只取签名，不上传任何东西）。

思路：先空参打一次，让接口/报错告诉我们缺什么；再按常见字段名逐个试。
"""

from __future__ import annotations

import json
import sys

from temu_bot.config import load_config
from temu_bot.dianxiaomi.client import DxmClient

LOGIN = sys.argv[1] if len(sys.argv) > 1 else "lovely"

GUESSES: list[tuple[str, dict]] = [
    ("空参", {}),
    ("fileName", {"fileName": "test.jpg"}),
    ("fileName+suffix", {"fileName": "test.jpg", "suffix": "jpg"}),
    (
        "常见组合A",
        {"fileName": "test.jpg", "fileType": "image/jpeg", "size": 12345, "suffix": "jpg"},
    ),
    (
        "常见组合B",
        {"name": "test.jpg", "type": "image/jpeg", "size": 12345},
    ),
    (
        "常见组合C",
        {"fileName": "test.jpg", "dir": "product/", "contentType": "image/jpeg"},
    ),
]

cfg = load_config()
with DxmClient(cfg, LOGIN) as client:
    for label, payload in GUESSES:
        try:
            resp = client._client.post("/api/cos/getSign.json", data=payload)
        except Exception as exc:  # noqa: BLE001
            print(f"[{label}] 请求异常：{exc}")
            continue
        body = (resp.text or "").strip()
        print(f"[{label}] HTTP {resp.status_code}")
        print(f"    载荷: {json.dumps(payload, ensure_ascii=False)}")
        print(f"    返回: {body[:500]}")
        print()
