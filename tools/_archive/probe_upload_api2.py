"""收窄 getSign 的参数：围绕已识别的 fileName/fileType/size/suffix 做变体扫描。"""

from __future__ import annotations

import json
import sys

from temu_bot.config import load_config
from temu_bot.dianxiaomi.client import DxmClient

LOGIN = sys.argv[1] if len(sys.argv) > 1 else "lovely"
BASE = {"fileName": "test.jpg", "fileType": "image/jpeg", "size": "12345", "suffix": "jpg"}

VARIANTS: list[tuple[str, dict]] = [
    ("全字符串(基线)", dict(BASE)),
    ("去掉 size", {k: v for k, v in BASE.items() if k != "size"}),
    ("去掉 suffix", {k: v for k, v in BASE.items() if k != "suffix"}),
    ("去掉 fileType", {k: v for k, v in BASE.items() if k != "fileType"}),
    ("size 数字", {**BASE, "size": 12345}),
    ("fileType=image/jpg", {**BASE, "fileType": "image/jpg"}),
    ("suffix=.jpg", {**BASE, "suffix": ".jpg"}),
    ("fileType 去掉", {**BASE, "fileType": ""}),
    ("加 fileSize", {**BASE, "fileSize": "12345"}),
    ("加 ext", {**BASE, "ext": "jpg"}),
    ("fileType=image/png", {**BASE, "fileType": "image/png", "fileName": "test.png", "suffix": "png"}),
    ("加 totalSize", {**BASE, "totalSize": "12345"}),
    ("加 md5", {**BASE, "md5": "d41d8cd98f00b204e9800998ecf8427e"}),
]

cfg = load_config()
with DxmClient(cfg, LOGIN) as client:
    for label, payload in VARIANTS:
        try:
            resp = client._client.post("/api/cos/getSign.json", data=payload)
        except Exception as exc:  # noqa: BLE001
            print(f"{label:22} 请求异常 {exc}")
            continue
        body = (resp.text or "").strip()
        if body.startswith("<!DOCTYPE"):
            short = "HTML 错误页（服务端异常）"
        else:
            try:
                j = json.loads(body)
                short = f"code={j.get('code')} msg={j.get('msg')}"
                if j.get("code") == 0 or (j.get("data") and str(j.get("code")) not in ("-1",)):
                    short += f"  ★ data={json.dumps(j.get('data'), ensure_ascii=False)[:220]}"
            except Exception:  # noqa: BLE001
                short = body[:160]
        flag = "  ★★" if "code=0" in short or "★ data" in short else ""
        print(f"{label:22} HTTP {resp.status_code}  {short}{flag}")
