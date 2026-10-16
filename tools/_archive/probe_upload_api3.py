"""锁定 fileType 的取值：它必填，但传 MIME 字符串会让服务端异常，多半是数字枚举。"""

from __future__ import annotations

import json
import sys

from temu_bot.config import load_config
from temu_bot.dianxiaomi.client import DxmClient

LOGIN = sys.argv[1] if len(sys.argv) > 1 else "lovely"

CANDIDATES = [0, 1, 2, 3, 4, 5, "1", "2", "image", "jpg", "JPEG", "picture", "goods", "product"]


def short(body: str, status: int) -> str:
    body = (body or "").strip()
    if body.startswith("<!DOCTYPE"):
        return "HTML 错误页"
    try:
        j = json.loads(body)
        s = f"code={j.get('code')} msg={j.get('msg')}"
        if j.get("data"):
            s += f"  ★data={json.dumps(j['data'], ensure_ascii=False)[:300]}"
        return s
    except Exception:  # noqa: BLE001
        return body[:160]


cfg = load_config()
with DxmClient(cfg, LOGIN) as client:
    print(f"{'fileType':14} {'HTTP':5} 结果")
    for val in CANDIDATES:
        payload = {"fileName": "test.jpg", "fileType": val, "size": "12345", "suffix": "jpg"}
        try:
            resp = client._client.post("/api/cos/getSign.json", data=payload)
        except Exception as exc:  # noqa: BLE001
            print(f"{str(val):14} ----  异常 {exc}")
            continue
        out = short(resp.text, resp.status_code)
        flag = "  ★★★" if "code=0" in out or "★data" in out else ""
        print(f"{str(val):14} {resp.status_code:<5} {out}{flag}")
