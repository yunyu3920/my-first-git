"""把商品 carousel（previewImgUrls）逐张编号打出来，看哪张是主图（只读）。

用法：
    python tools\\dump_previews.py hat-tshirt 220114595067275322 220114595101444742
"""

from __future__ import annotations

import json
import sys

from temu_bot.config import load_config
from temu_bot.dianxiaomi.client import DxmClient

argv = sys.argv[1:]
login = argv[0] if argv else "hat-tshirt"
ids = argv[1:]

cfg = load_config()
with DxmClient(cfg, login) as client:
    for product_id in ids:
        product = ((client.product_edit(product_id).get("data") or {}).get("product")) or {}
        if not product:
            print(f"{product_id}: 读不到")
            continue

        print("=" * 78)
        print(f"商品 {product_id}｜{product.get('productName')}")
        print(f"  素材图 materialImgUrl = {product.get('materialImgUrl')}")

        for key in ("mainProductSkuSpecReqs", "mainProductSkuSpecReqsList"):
            raw = product.get(key)
            if raw is None:
                print(f"  {key}: 没有这个字段")
                continue
            items = json.loads(raw) if isinstance(raw, str) else raw
            for i, entry in enumerate(items):
                if not isinstance(entry, dict):
                    continue
                print(f"  {key}[{i}] {entry.get('parentSpecName')}={entry.get('specName')}")
                urls = [u for u in str(entry.get("previewImgUrls") or "").split("|") if u]
                print(f"    carousel 共 {len(urls)} 张：")
                for n, url in enumerate(urls):
                    tag = "  ← 第 1 张（主图？）" if n == 0 else ""
                    print(f"      [{n}] {url}{tag}")

        raw = product.get("variationListStr")
        if isinstance(raw, str):
            items = json.loads(raw)
            thumbs = {e.get("thumbUrl") for e in items if isinstance(e, dict)}
            print(f"  variationListStr 共 {len(items)} 条；thumbUrl 有 {len(thumbs)} 种：")
            for t in sorted(thumbs):
                print(f"      {t}")
