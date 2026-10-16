"""真跑若干行（复制 → 传图换图 → 改标题 → 保存 → 回写表格）。

这是**真写**：会往图片空间传图、会在店小秘里建出商品。（复制不扣米豆，实测过。）
默认 op=1（仅保存，**不发布**）。

**商品ID 与状态由主流程自己写回表格**（`WRITEBACK` 步），跑完不用再手动写。
这里再把明细追加进 `data/state/live_runs.jsonl` 当作账本——万一回写失败了，
`temu-bot table writeback --live` 能从它把成功过的行补上。

用法：
    python tools\\live_run_rows.py hat-tshirt 2 3 4 5 6 7          # op=1 仅保存
    python tools\\live_run_rows.py hat-tshirt 2 --op 2             # 立即发布（慎用）

一行失败就**停**：失败往往不是单行的问题，继续跑会把同一处错放大 6 遍，
而且会在待发布里留下几个"复制了但没改好"的商品。停下来的位置会明确打出来。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from temu_bot.config import load_config
from temu_bot.dianxiaomi.client import OP_SAVE
from temu_bot.listing import OP_LABELS, ListingPipeline
from temu_bot.sheet.table import SheetTable

argv = sys.argv[1:]
if not argv:
    raise SystemExit(__doc__)

login = argv[0]
rest = argv[1:]
op = OP_SAVE
if "--op" in rest:
    i = rest.index("--op")
    op = int(rest[i + 1])
    rest = rest[:i] + rest[i + 2 :]
wanted = [int(x) for x in rest]

cfg = load_config()
table = SheetTable.from_config(cfg)
rows = {r.row_index: r for r in table.iter_rows()}

missing = [i for i in wanted if i not in rows]
if missing:
    raise SystemExit(f"表里没有这些行：{missing}")

print(f"登录身份 {login}｜op={op}（{OP_LABELS.get(op, '?')}）｜共 {len(wanted)} 行")
print("**真写**：会传图、会建商品（复制不扣米豆，实测过）\n")

results: list[dict] = []
for index in wanted:
    row = rows[index]
    print("=" * 72)
    print(f"第 {index} 行｜{row.shop}｜SPU {row.template_spu}｜{row.main_image}")

    outcome = ListingPipeline(cfg, row, login=login, live=True, op=op).run()
    for step in outcome.steps:
        print(f"  {step.line()}")

    if outcome.writeback_failed:
        print(
            f"   ⚠ 商品ID/状态没能写回表格（多半是表开着 Excel）。"
            f"别重跑这一行；关掉表后跑 temu-bot table writeback --live 补上。"
        )

    results.append(
        {
            "row_index": index,
            "ok": not outcome.failed,
            "product_id": outcome.product_id,
            "writeback_ok": outcome.writeback_ok,
            "steps": outcome.as_dict()["steps"],
        }
    )

    if outcome.failed:
        print(f"\n❌ 第 {index} 行失败，**就此停下**（后面的行没跑）")
        if outcome.product_id:
            print(f"   → 待发布里已经建出了商品 {outcome.product_id}，需要你去店小秘看一眼")
        print(f"   剩下的行：{[i for i in wanted if i > index]}")
        break

out = Path("data/state/live_runs.jsonl")
out.parent.mkdir(parents=True, exist_ok=True)
with out.open("a", encoding="utf-8") as fh:
    for item in results:
        fh.write(json.dumps(item, ensure_ascii=False) + "\n")

ok = [r for r in results if r["ok"]]
print("=" * 72)
print(f"跑完 {len(results)} 行：成功 {len(ok)}，失败 {len(results) - len(ok)}")
for item in ok:
    print(f"  ✅ 第 {item['row_index']} 行 → 商品 {item['product_id']}")
print(f"\n明细已追加到 {out}")
