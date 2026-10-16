"""逐格比对「收集生成的新表」与「用户原有表」，确认产出格式一致（只读）。"""

from __future__ import annotations

import sys
from pathlib import Path

import openpyxl

NEW = Path(sys.argv[1])
OLD = Path(sys.argv[2])

# 收集阶段会填的列（其余列留给上架回填，两边都应为空）
COLLECT_COLS = {1: "文件夹路径", 2: "主图", 8: "素材图", 9: "前缀编码", 10: "店铺名", 11: "模板SPU", 12: "主图索引"}
# 上架阶段才填的列
LATER_COLS = {13: "商品ID", 14: "状态", 17: "中文标题", 18: "英文标题"}


def load(path: Path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    rows = list(wb["Sheet1"].iter_rows(values_only=True))
    wb.close()
    header, data = rows[0], rows[1:]
    return header, data


nh, nd = load(NEW)
oh, od = load(OLD)

print(f"新表: {NEW.name}")
print(f"旧表: {OLD.name}")
print(f"表头是否一致: {list(nh) == list(oh)}")
print(f"行数: 新 {len(nd)} / 旧 {len(od)}")
print()

if len(nd) != len(od):
    print("⚠ 行数不同，逐行比对可能错位")
    print()

n = min(len(nd), len(od))
mismatch = 0
for col, label in COLLECT_COLS.items():
    diffs = []
    for i in range(n):
        a = nd[i][col - 1] if len(nd[i]) >= col else None
        b = od[i][col - 1] if len(od[i]) >= col else None
        sa = "" if a is None else str(a).strip()
        sb = "" if b is None else str(b).strip()
        if sa != sb:
            diffs.append((i + 2, sa, sb))
    mark = "✅" if not diffs else "❌"
    print(f"{mark} 第{col:2d}列 {label}: 一致 {n - len(diffs)}/{n}", end="")
    if diffs:
        mismatch += len(diffs)
        print(f"　不一致 {len(diffs)} 处，前 3：")
        for row_no, a, b in diffs[:3]:
            print(f"      第{row_no}行　新={a!r}　旧={b!r}")
    else:
        print()

print()
print("=== 上架阶段才填的列（新表应为空）===")
for col, label in LATER_COLS.items():
    filled = sum(1 for r in nd if len(r) >= col and r[col - 1] not in (None, ""))
    mark = "✅" if filled == 0 else "⚠"
    print(f"{mark} 第{col:2d}列 {label}: 新表非空 {filled}/{len(nd)}（旧表非空 {sum(1 for r in od if len(r) >= col and r[col-1] not in (None,''))})")

print()
print("结论：", "收集产出与你的表完全一致 ✅" if mismatch == 0 else f"有 {mismatch} 处不一致 ❌")
