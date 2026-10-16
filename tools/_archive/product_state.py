"""把一个商品的状态相关字段全列出来，并确认它在列表的哪个位置（只读）。"""

from __future__ import annotations

import sys

from temu_bot.config import load_config
from temu_bot.dianxiaomi.client import DxmClient

PID = sys.argv[1] if len(sys.argv) > 1 else "184807703165779045"
LOGIN = sys.argv[2] if len(sys.argv) > 2 else "lovely"

cfg = load_config()
with DxmClient(cfg, LOGIN) as client:
    product = ((client.product_edit(PID).get("data") or {}).get("product")) or {}
    print(f"=== {PID} 的状态相关字段 ===")
    for k, v in sorted(product.items()):
        if any(t in k.lower() for t in ("state", "status", "time", "err", "fail", "reject", "audit")):
            print(f"  {k}: {str(v)[:200]}")

    print("\n=== 在各列表里找它 ===")
    for state in ("online", "offline", "draft"):
        found = None
        page_no = 1
        while page_no <= 40:
            d = client.list_products(dxm_state=state, page_no=page_no, page_size=50)
            page = (d or {}).get("page") or {}
            rows = page.get("list") or []
            if not rows:
                break
            for r in rows:
                if str(r.get("id")) == str(PID):
                    found = r
                    break
            if found:
                break
            total_page = int(page.get("totalPage") or 1)
            if page_no >= total_page:
                break
            page_no += 1

        if found:
            print(f"  ✅ 在 [{state}] 第 {page_no} 页找到")
            for k in ("id", "productId", "productName", "dxmState", "shopId", "updateTime"):
                print(f"      {k}: {str(found.get(k))[:120]}")
        else:
            print(f"  ❌ 不在 [{state}]（翻到第 {page_no} 页）")
