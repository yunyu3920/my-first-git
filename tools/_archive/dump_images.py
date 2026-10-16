"""把一个商品的图片相关字段原样打出来（只读）。

看这几个字段到底长什么样：
    mainProductSkuSpecReqs  —— SKC 轮播图（previewImgUrls，竖线拼多张）
    variationListStr        —— SKU 规格（thumbUrl 缩略图 + 颜色等属性）
    materialImgUrl          —— 素材图（顶层）

用法：
    python tools\\dump_images.py hat-tshirt 220114595067275322
"""

from __future__ import annotations

import json
import sys

from temu_bot.config import load_config
from temu_bot.dianxiaomi.client import DxmClient

login = sys.argv[1] if len(sys.argv) > 1 else "hat-tshirt"
product_id = sys.argv[2] if len(sys.argv) > 2 else ""

if not product_id:
    raise SystemExit(__doc__)

cfg = load_config()
with DxmClient(cfg, login) as client:
    product = ((client.product_edit(product_id).get("data") or {}).get("product")) or {}

if not product:
    raise SystemExit(f"读不到商品 {product_id}")

print(f"商品 {product_id}")
print(f"  productName      = {product.get('productName')}")
print(f"  materialImgUrl   = {product.get('materialImgUrl')}")

for key in ("mainProductSkuSpecReqs", "mainProductSkuSpecReqsList",
            "variationListStr", "variations"):
    raw = product.get(key)
    print(f"\n=== {key} ===  (类型 {type(raw).__name__})")
    if raw is None:
        print("  （这个商品没有这个字段）")
        continue
    try:
        items = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        print(f"  （不是 JSON：{str(raw)[:200]}）")
        continue
    if not isinstance(items, list):
        print(f"  （不是列表：{str(items)[:200]}）")
        continue
    print(f"  共 {len(items)} 条")
    for i, entry in enumerate(items):
        if not isinstance(entry, dict):
            print(f"  [{i}] {entry!r}")
            continue
        # 只把"看起来像图片位或规格属性"的键打出来，别的太长
        keys = [k for k in entry if any(
            t in k.lower() for t in ("img", "url", "spec", "name", "value", "color", "id")
        )]
        print(f"  [{i}]")
        for k in keys:
            v = entry[k]
            text = str(v)
            if len(text) > 260:
                text = text[:260] + f"…（共 {len(text)} 字符）"
            print(f"       {k} = {text}")

# 顶层还有哪些带 img 的键
print("\n=== 顶层其它图片相关字段 ===")
for key, value in product.items():
    if "img" in key.lower() or "pic" in key.lower() or "image" in key.lower():
        if key in ("materialImgUrl",):
            continue
        text = str(value)
        if len(text) > 300:
            text = text[:300] + "…"
        print(f"  {key} = {text}")
