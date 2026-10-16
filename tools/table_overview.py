"""当前信息表格的概况（只读）：一共几行、几个店、多少行有商品ID。"""

from __future__ import annotations

from collections import Counter

from temu_bot.config import load_config
from temu_bot.sheet.table import SheetTable

cfg = load_config()
print(f"表：{cfg.project.table}")
rows = list(SheetTable.from_config(cfg).iter_rows())
print(f"共 {len(rows)} 行")

by_shop = Counter(r.shop for r in rows)
print(f"店铺分布：{dict(by_shop)}")
print(f"有商品ID 的行：{sum(1 for r in rows if r.product_id)}")
print(f"没有商品ID 的行：{sum(1 for r in rows if not r.product_id)}")

status = Counter(r.status or "(空)" for r in rows)
print(f"状态分布：{dict(status)}")

folders = Counter(r.folder for r in rows)
print("文件夹：")
for folder, n in folders.most_common(10):
    print(f"  {n:>4}  {folder}")

missing_material = [r.row_index for r in rows if not r.material_image]
print(f"缺素材图的行（{len(missing_material)}）：{missing_material[:20]}")
