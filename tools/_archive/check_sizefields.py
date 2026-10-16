"""检查商品的尺码表相关字段是否有值（排除"是我写坏的"）。"""

from __future__ import annotations

import json
import sys

from temu_bot.config import load_config
from temu_bot.dianxiaomi.client import DxmClient, SAVE_FIELD_TYPES

PID = sys.argv[1] if len(sys.argv) > 1 else "184807703165779045"
LOGIN = sys.argv[2] if len(sys.argv) > 2 else "lovely"

cfg = load_config()
with DxmClient(cfg, LOGIN) as client:
    product = ((client.product_edit(PID).get("data") or {}).get("product")) or {}

    print("=== 尺码表相关字段 ===")
    for k in ("sizeCharts", "sizeTemplateIds", "sizeTemplateId", "sizeChartId", "region1ShortNames", "region2Id"):
        if k in product:
            v = product.get(k)
            print(f"  {k}: {str(v)[:300]}")

    print("\n=== 会被 save_product 发回去的 39 个字段，逐个看有没有值 ===")
    empty, filled = [], []
    for key in SAVE_FIELD_TYPES:
        v = product.get(key)
        if v in (None, "", "0", 0, "[]", "{}"):
            empty.append(f"{key}={v!r}")
        else:
            filled.append(f"{key}={str(v)[:60]!r}")

    print(f"\n有值（{len(filled)}）:")
    for line in filled:
        print(f"  {line}")
    print(f"\n空值（{len(empty)}）:")
    for line in empty:
        print(f"  {line}")

    # 关键：尺码表在不在
    print("\n=== 结论 ===")
    sc = product.get("sizeCharts")
    sti = product.get("sizeTemplateIds")
    print(f"  sizeCharts 有值？{'是' if sc not in (None, '', '[]', '{}') else '否'} ({str(sc)[:80]})")
    print(f"  sizeTemplateIds 有值？{'是' if sti not in (None, '', '[]', '{}') else '否'} ({str(sti)[:80]})")

    # 顺便看看商品里所有含 size 的字段
    print("\n=== 所有含 size 的字段 ===")
    for k, v in sorted(product.items()):
        if "size" in k.lower():
            print(f"  {k}: {json.dumps(v, ensure_ascii=False)[:200] if not isinstance(v, str) else v[:200]}")
