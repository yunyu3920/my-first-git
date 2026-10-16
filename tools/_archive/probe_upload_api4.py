"""用 JS 里挖到的真实参数试 getSign（只取签名，不上传文件）。"""

from __future__ import annotations

import json
import sys

from temu_bot.config import load_config
from temu_bot.dianxiaomi.client import DxmClient

LOGIN = sys.argv[1] if len(sys.argv) > 1 else "lovely"

TRIES = [
    ("JS 原样", {"bucket": "wxalbum", "region": "ap-shanghai", "fileName": "test.jpg"}),
    ("换扩展名", {"bucket": "wxalbum", "region": "ap-shanghai", "fileName": "00742093597.jpg"}),
    ("没 region", {"bucket": "wxalbum", "fileName": "test.jpg"}),
    ("没 bucket", {"region": "ap-shanghai", "fileName": "test.jpg"}),
]

cfg = load_config()
with DxmClient(cfg, LOGIN) as client:
    for label, payload in TRIES:
        resp = client._client.post("/api/cos/getSign.json", data=payload)
        body = (resp.text or "").strip()
        print(f"[{label}] HTTP {resp.status_code}")
        print(f"    载荷: {json.dumps(payload, ensure_ascii=False)}")
        if body.startswith("<!DOCTYPE"):
            print("    返回: HTML 错误页")
        else:
            try:
                j = json.loads(body)
                print(f"    code={j.get('code')} msg={j.get('msg')}")
                for k, v in (j.get("data") or {}).items() if isinstance(j.get("data"), dict) else []:
                    print(f"      {k} = {str(v)[:120]}")
                if not isinstance(j.get("data"), dict):
                    print(f"    data = {json.dumps(j.get('data'), ensure_ascii=False)[:400]}")
            except Exception:  # noqa: BLE001
                print(f"    返回: {body[:400]}")
        print()
