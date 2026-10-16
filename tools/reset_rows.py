"""把表里若干行的**回写列清空**（商品ID / 状态 / 问题项），好让它们重新变成"新建"。

**这是破坏性操作**：清掉商品ID 之后，流水线就不认得这些行上过架了，再跑就是**再建一遍**。
所以：
  1. 默认干跑，只打印将要清什么；
  2. 清之前把被清掉的商品ID **存一份到 data/state/**，免得彻底找不回来；
  3. 真写走 `sheet/guard.py`（先确认表没被 Excel 打开 + 备份 + 原子替换）。

用法：
    python tools\\reset_rows.py 2 3 4 5 6 7            # 干跑，看会清什么
    python tools\\reset_rows.py 2 3 4 5 6 7 --live     # 真清
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from temu_bot.config import load_config
from temu_bot.sheet.guard import CellUpdate, TableBusyError, assert_not_busy, write_updates
from temu_bot.sheet.schema import COL_PROBLEM, COL_PRODUCT_ID, COL_STATUS, COL_UNIQUE_KEY
from temu_bot.sheet.table import SheetTable

argv = sys.argv[1:]
live = "--live" in argv
indexes = sorted(int(a) for a in argv if a.isdigit())

if not indexes:
    raise SystemExit(__doc__)

cfg = load_config()
rows = {r.row_index: r for r in SheetTable.from_config(cfg).iter_rows()}

missing = [i for i in indexes if i not in rows]
if missing:
    raise SystemExit(f"表里没有这些行：{missing}")

# 要被清掉的东西先记下来
rescued = {
    str(i): {
        "product_id": rows[i].product_id,
        "status": rows[i].status,
        "problem": rows[i].problem,
        "main_image": rows[i].main_image,
    }
    for i in indexes
}

print(f"表：{cfg.project.table}")
print(f"{'真写' if live else '干跑'}｜将清空第 {indexes} 行的 商品ID / 状态 / 问题项\n")
for i in indexes:
    row = rows[i]
    print(f"  第 {i} 行  {row.main_image}")
    print(f"     商品ID  {row.product_id or '(空)'}")
    print(f"     状态    {row.status or '(空)'}")
    print(f"     问题项  {(row.problem or '(空)')[:60]}")

updates = [
    CellUpdate(
        row_index=i,
        fields={COL_PRODUCT_ID: None, COL_STATUS: None, COL_PROBLEM: None, COL_UNIQUE_KEY: None},
    )
    for i in indexes
    if rows[i].product_id or rows[i].status or rows[i].problem
]

if not updates:
    print("\n这些行本来就是空的，没什么可清。")
    raise SystemExit(0)

if not live:
    print("\n干跑：确认无误再加 --live（写前会备份，被清掉的商品ID 也会存一份）。")
    raise SystemExit(0)

# 存档：这些商品ID 是"店里还挂着的旧商品"，用户之后要靠它去删
out = Path(cfg.data_dir) / "state" / f"reset_{datetime.now():%Y%m%d-%H%M%S}.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(rescued, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n被清掉的旧商品ID 已存档：{out}")

try:
    assert_not_busy(cfg.project.table)
    backup_path = write_updates(cfg, updates)
except TableBusyError as exc:
    raise SystemExit(str(exc)) from exc

print(f"已清空 {len(updates)} 行｜表格备份 {backup_path}")
print("\n⚠ 店里那 6 个旧商品还在，表格已经不再追踪它们了——记得去店小秘删掉。")
