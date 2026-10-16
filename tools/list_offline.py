"""把待发布里的商品列出来（只读），并标出哪些是工具建的、货号对不对。"""

from __future__ import annotations

import json
import sys

from temu_bot.config import load_config
from temu_bot.dianxiaomi.client import DxmClient
from temu_bot.sheet.table import SheetTable

login = sys.argv[1] if len(sys.argv) > 1 else "hat-tshirt"
cfg = load_config()

# 表格里认的商品ID
tracked = {r.product_id: r.row_index for r in SheetTable.from_config(cfg).iter_rows() if r.product_id}

with DxmClient(cfg, login) as client:
    rows: list[dict] = []
    seen: set[str] = set()
    page_no = 1
    while True:
        # 注意参数名是 page_no；写成 page= 会被 **extra 吃掉、悄悄永远返回第 1 页
        data = client.list_products(dxm_state="offline", page_size=100, page_no=page_no)
        page_data = data.get("page") or {}
        batch = page_data.get("list") or []
        for row in batch:
            pid = str(row.get("id"))
            if pid not in seen:
                seen.add(pid)
                rows.append(row)
        total = int(page_data.get("totalSize") or 0)
        if not batch or len(seen) >= total:
            break
        page_no += 1

print(f"待发布里共 {len(rows)} 个商品\n")
print(f"{'商品ID':<20} {'表格':<6} {'SKU货号':<14} 标题")
for row in sorted(rows, key=lambda r: str(r.get("id"))):
    pid = str(row.get("id"))
    mark = f"第{tracked[pid]}行" if pid in tracked else "—"
    try:
        product = ((client.product_edit(pid).get("data") or {}).get("product")) or {}
        var = json.loads(product.get("variationListStr") or "[]")
        codes = sorted({str(e.get("extCode")) for e in var})
        code_text = ",".join(codes)
    except Exception:  # noqa: BLE001
        code_text = "?"
    print(f"{pid:<20} {mark:<6} {code_text:<14} {str(row.get('productName'))[:26]}")
