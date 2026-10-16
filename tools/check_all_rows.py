"""把表里每一行的图片都按 Temu 规格查一遍（只读，不出图）。"""

from __future__ import annotations

from temu_bot.config import load_config
from temu_bot.imaging.pipeline import process_row
from temu_bot.sheet.table import SheetTable

cfg = load_config()
rows = list(SheetTable.from_config(cfg).iter_rows())

bad = 0
for row in rows:
    result = process_row(cfg, row, dry_run=True)
    ok = not result.failed
    if not ok:
        bad += 1
    print(f"第 {row.row_index} 行  {'✅' if ok else '❌'}  {row.main_image}")
    for item in result.items:
        print(f"    [{item.slot}] {item.line()}")

print(f"\n共 {len(rows)} 行，图片不合格 {bad} 行")
