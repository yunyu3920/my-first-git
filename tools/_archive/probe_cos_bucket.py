"""试不同的 bucket，看哪个产出商品图那种 /product/open/ 路径（只取签名，不上传）。"""

from __future__ import annotations

import json
import sys

from temu_bot.config import load_config
from temu_bot.dianxiaomi.client import DxmClient

LOGIN = sys.argv[1] if len(sys.argv) > 1 else "lovely"

BUCKETS = [
    "wxalbum", "product", "goods", "temu", "pop", "dxmproduct", "productopen",
    "open", "image", "album", "dxm", "popTemu", "item", "sku", "cms", "public",
]
REGIONS = ["ap-shanghai", "ap-guangzhou", "ap-beijing"]

cfg = load_config()
with DxmClient(cfg, LOGIN) as client:
    print(f"{'bucket':14} {'region':14} url")
    seen = set()
    for bucket in BUCKETS:
        for region in REGIONS:
            try:
                r = client.cos_get_sign(bucket=bucket, region=region, fileName="t.jpg")
            except Exception as exc:  # noqa: BLE001
                continue
            data = (r or {}).get("data") or {}
            url = data.get("url") or ""
            if (r or {}).get("code") != 0 or not url:
                if bucket == BUCKETS[0] and region == REGIONS[0]:
                    print(f"{bucket:14} {region:14} ❌ {(r or {}).get('msg')}")
                continue
            key = url.split("/")[3] if len(url.split("/")) > 3 else url
            if key in seen:
                continue
            seen.add(key)
            star = "  ★★ 商品图路径！" if "/product/" in url else ""
            print(f"{bucket:14} {region:14} {url[:110]}{star}")
