"""把表里每一行都用同一个登录身份干跑一遍（**只读，不写任何数据**）。

用法：
    python tools\\dry_run_all.py hat-tshirt
"""

from __future__ import annotations

import sys

from temu_bot.config import load_config
from temu_bot.listing import ListingPipeline
from temu_bot.sheet.schema import SheetRow
from temu_bot.sheet.table import SheetTable

login = sys.argv[1] if len(sys.argv) > 1 else "hat-tshirt"
only = int(sys.argv[2]) if len(sys.argv) > 2 else None

cfg = load_config()
rows = list(SheetTable.from_config(cfg).iter_rows())
if only:
    rows = [r for r in rows if r.row_index == only]

print(f"登录身份 {login}；共 {len(rows)} 行；**干跑**\n")

bad = 0
for row in rows:
    outcome = ListingPipeline(cfg, row, login=login, live=False, op=1).run()
    mark = "❌" if outcome.failed else "✅"
    if outcome.failed:
        bad += 1
    print(f"{mark} 第 {row.row_index} 行｜{row.shop}｜SPU {row.template_spu}｜{row.main_image}")
    for step in outcome.steps:
        print(f"     {step.line()}")

print(f"\n共 {len(rows)} 行，干跑失败 {bad} 行")
