"""把一批商品的 SKU 货号（extCode）打出来对比（只读）。

用来回答"页面里看到的是 99689509419，到底是哪个商品"这类问题——
把模板、新那批、旧那批一起列出来，一眼看出谁还是老货号。
"""

from __future__ import annotations

import json
import sys

from temu_bot.config import load_config
from temu_bot.dianxiaomi.client import DxmClient

TEMPLATE = "220114595067275322"
NEW = [
    "220114595101750194",
    "220114595101750230",
    "220114595101750252",
    "220114595101750288",
    "220114595101750326",
    "220114595101750372",
]
OLD = [
    "220114595101444742",
    "220114595101444772",
    "220114595101444796",
    "220114595101444826",
    "220114595101444850",
    "220114595101444864",
]

argv = sys.argv[1:]
login = "hat-tshirt"
if argv and not argv[0].isdigit():
    login = argv[0]
    argv = argv[1:]
cfg = load_config()

if argv:
    # 给了商品ID 就只查这几个（比如"页面上这个商品到底挂的什么货号"）
    groups = [("指定商品", argv)]
else:
    groups = [
        ("模板（它自己该是 99689509419，我们不动它）", [TEMPLATE]),
        ("新那批（表格里认的，货号应该各不相同）", NEW),
        ("旧那批（重复的，等着删，还是 99689509419）", OLD),
    ]

with DxmClient(cfg, login) as client:
    for title, ids in groups:
        print(f"\n=== {title} ===")
        for pid in ids:
            product = ((client.product_edit(pid).get("data") or {}).get("product")) or {}
            if not product:
                print(f"  {pid}  读不到")
                continue
            var = json.loads(product.get("variationListStr") or "[]")
            skc = json.loads(product.get("mainProductSkuSpecReqs") or "[]")
            sku_codes = sorted({str(e.get("extCode")) for e in var})
            skc_codes = sorted({str(e.get("extCode")) for e in skc})
            print(
                f"  {pid}  SKU货号={sku_codes}  SKC货号={skc_codes}"
                f"  dxmState={product.get('dxmState')}"
                f"  {str(product.get('productName'))[:22]}"
            )
