"""在商品 JSON 里定位某张 CDN 图挂在哪个字段上（只读）。"""

from __future__ import annotations

import json
import sys

from temu_bot.config import load_config
from temu_bot.dianxiaomi.client import DxmClient

PID = sys.argv[1] if len(sys.argv) > 1 else "184807703165779045"
NEEDLE = sys.argv[2] if len(sys.argv) > 2 else "6786e1f477ee4de494aae4e85c9fff33"
LOGIN = sys.argv[3] if len(sys.argv) > 3 else "lovely"

cfg = load_config()
with DxmClient(cfg, LOGIN) as client:
    product = ((client.product_edit(PID).get("data") or {}).get("product")) or {}

text = json.dumps(product, ensure_ascii=False)
print(f"商品 JSON 总长 {len(text)} 字符")
print(f"报错里那张图 {NEEDLE[:12]}… 出现次数: {text.count(NEEDLE)}\n")


def walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            walk(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            walk(v, f"{path}[{i}]")
    elif isinstance(obj, str) and NEEDLE in obj:
        print(f"  ★ {path} = {obj}")


print("=== 含该图的字段 ===")
walk(product)

print("\n=== 所有 goods.jpeg / 图片 URL（按字段名）===")
seen = set()
def walk2(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            walk2(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            walk2(v, f"{path}[{i}]")
    elif isinstance(obj, str) and "cdnfe.com" in obj and obj not in seen:
        seen.add(obj)
        print(f"  {path} = {obj}")
walk2(product)
