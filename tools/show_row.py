"""看一眼某一行在工具眼里长什么样（只读）。"""

from __future__ import annotations

import sys

from temu_bot.config import load_config
from temu_bot.sheet.table import SheetTable

index = int(sys.argv[1]) if len(sys.argv) > 1 else 32

cfg = load_config()
rows = list(SheetTable.from_config(cfg).iter_rows(row_index=index))
if not rows:
    raise SystemExit(f"没有第 {index} 行")
row = rows[0]

print(f"第 {row.row_index} 行")
print(f"  folder        = {row.folder}")
print(f"  shop          = {row.shop}")
print(f"  template_spu  = {row.template_spu}")
print(f"  product_id    = {row.product_id!r}")
print(f"  status        = {row.status!r}")
print(f"  main_image    = {row.main_image!r}")
print(f"  material_image= {row.material_image!r}")
print(f"  images        = {row.images}")
print(f"  title_cn      = {row.title_cn!r}")
print(f"  title_en      = {row.title_en!r}")
print("  raw（非空列）:")
for key, value in row.raw.items():
    if value:
        print(f"    {key!r}: {value!r}")
