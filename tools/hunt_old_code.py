"""在一个商品的**全部字段**里搜某个货号，看还有谁留着它（只读）。

页面显示的货号万一不是 `extCode` 而是别的字段，这里能立刻看出来。
"""

from __future__ import annotations

import json
import sys

from temu_bot.config import load_config
from temu_bot.dianxiaomi.client import DxmClient

argv = sys.argv[1:]
login = argv[0] if argv else "hat-tshirt"
product_id = argv[1] if len(argv) > 1 else ""
needle = argv[2] if len(argv) > 2 else "99689509419"

if not product_id:
    raise SystemExit(__doc__)

cfg = load_config()
with DxmClient(cfg, login) as client:
    product = ((client.product_edit(product_id).get("data") or {}).get("product")) or {}

print(f"商品 {product_id}｜{product.get('productName')}")
print(f"在全部字段里搜 {needle!r}\n")

hits: list[str] = []


def walk(node, path: str) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            walk(value, f"{path}.{key}" if path else key)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            walk(value, f"{path}[{i}]")
    elif needle in str(node):
        hits.append(f"{path} = {node!r}")


walk(product, "")
if hits:
    for h in hits:
        print(f"  ⚠ 还留着：{h}")
else:
    print(f"  ✅ 整个商品里没有任何字段还带着 {needle}")

print("\n=== variationListStr[0] 整条 ===")
raw = product.get("variationListStr")
if isinstance(raw, str):
    items = json.loads(raw)
    if items:
        for key, value in items[0].items():
            print(f"  {key} = {value!r}")
