"""把商品的 SKU / 货号相关字段**一个不漏**地打出来（只读）。

之前 dump_images.py 按关键字过滤过键，可能漏。这里把每条规格、每个 SKU 的**全部键**
都列出来，重点找"货号"那个字段到底叫什么。
"""

from __future__ import annotations

import json
import sys

from temu_bot.config import load_config
from temu_bot.dianxiaomi.client import DxmClient

argv = sys.argv[1:]
login = argv[0] if argv else "hat-tshirt"
product_id = argv[1] if len(argv) > 1 else ""

if not product_id:
    raise SystemExit(__doc__)

cfg = load_config()
with DxmClient(cfg, login) as client:
    product = ((client.product_edit(product_id).get("data") or {}).get("product")) or {}

print(f"商品 {product_id}｜{product.get('productName')}\n")

print("=== 顶层全部键（按字母序）===")
for key in sorted(product):
    value = product[key]
    text = str(value)
    if len(text) > 120:
        text = text[:120] + f"…（{len(text)} 字符）"
    print(f"  {key} = {text}")

for field in ("mainProductSkuSpecReqs", "mainProductSkuSpecReqsList"):
    raw = product.get(field)
    if raw is None:
        continue
    items = json.loads(raw) if isinstance(raw, str) else raw
    print(f"\n=== {field}：{len(items)} 条 ===")
    for i, entry in enumerate(items):
        print(f"  [{i}] 全部键：")
        if isinstance(entry, dict):
            for key, value in entry.items():
                print(f"       {key} = {value!r}")

raw = product.get("variationListStr")
if isinstance(raw, str):
    items = json.loads(raw)
    print(f"\n=== variationListStr：{len(items)} 条（只详列第 1 条 + 货号类字段）===")
    if items:
        print("  [0] 全部键：")
        for key, value in items[0].items():
            print(f"       {key} = {value!r}")
    print("\n  各条里含 code/sn/sku/货 的键：")
    for i, entry in enumerate(items):
        if not isinstance(entry, dict):
            continue
        interesting = {
            k: v
            for k, v in entry.items()
            if any(t in k.lower() for t in ("code", "sn", "sku", "outer", "no", "number"))
        }
        print(f"    [{i}] {interesting}")
