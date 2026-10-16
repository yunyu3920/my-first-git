"""全表主图规格审计（只读）：找出所有不满足 Temu 3:4 / 1340x1785 / <=2MB 的行。"""

from __future__ import annotations

import collections
import sys

import openpyxl
from PIL import Image

PATH = r"C:\Users\A\Desktop\Temu\商品信息表.xlsx"
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 0


def cell(row, n):
    v = row[n - 1] if len(row) >= n else None
    return "" if v is None else str(v).strip()


wb = openpyxl.load_workbook(PATH, read_only=True, data_only=True)
ws = wb["Sheet1"]

sizes: collections.Counter[str] = collections.Counter()
bad: list[tuple[int, str, str, str]] = []
checked = 0

for idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
    from pathlib import Path

    folder = cell(row, 1)
    main = cell(row, 2)
    if not folder or not main:
        continue
    p = Path(folder) / main
    if not p.exists():
        bad.append((idx, main, "文件不存在", ""))
        continue
    checked += 1
    try:
        with Image.open(p) as im:
            w, h = im.size
    except Exception as exc:  # noqa: BLE001
        bad.append((idx, main, f"打不开：{exc}", ""))
        continue
    key = f"{w}x{h}"
    sizes[key] += 1
    problems = []
    if abs(w / h - 0.75) > 0.01:
        problems.append(f"比例 {w/h:.4f}")
    if w < 1340 or h < 1785:
        problems.append(f"小于 1340x1785")
    nbytes = p.stat().st_size
    if nbytes > 2 * 1024 * 1024:
        problems.append(f"{nbytes/1024/1024:.2f}MB")
    if problems:
        bad.append((idx, main, key, "；".join(problems)))

print(f"检查了 {checked} 行主图\n")
print("=== 尺寸分布 ===")
for k, v in sizes.most_common():
    print(f"  {k}: {v} 行")

print(f"\n=== 不合规（{len(bad)} 行）===")
for idx, name, key, why in (bad[:LIMIT] if LIMIT else bad):
    print(f"  行{idx}: {name}  {key}  {why}")

wb.close()
