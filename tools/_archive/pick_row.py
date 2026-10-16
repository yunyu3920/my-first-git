"""扫描整表：状态分布 + 店铺分布，并挑几行"待处理"的候选（只读）。"""

from __future__ import annotations

import collections

import openpyxl

PATH = r"C:\Users\A\Desktop\Temu\商品信息表.xlsx"

wb = openpyxl.load_workbook(PATH, read_only=True, data_only=True)
ws = wb["Sheet1"]


def cell(row, n):
    v = row[n - 1] if len(row) >= n else None
    return "" if v is None else str(v).replace("\n", "/").replace("\r", "").strip()


status = collections.Counter()
shop = collections.Counter()
pending: list[tuple[int, str, str, str, str, str]] = []
sandbox_ok: list[tuple[int, str, str]] = []

for idx, r in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
    st = cell(r, 14)
    sp = cell(r, 10)
    status[st or "(空)"] += 1
    shop[sp or "(空)"] += 1
    if st != "成功" and sp:
        pending.append((idx, sp, cell(r, 11), cell(r, 2), st, cell(r, 17)[:20]))
    if idx <= 40 and cell(r, 1) and cell(r, 2):
        sandbox_ok.append((idx, sp, cell(r, 2)))

print("=== 状态分布 ===")
for k, v in status.most_common():
    print(f"  {k!r}: {v}")
print("\n=== 店铺分布 ===")
for k, v in shop.most_common():
    print(f"  {k!r}: {v}")

print(f"\n=== 非「成功」且有店铺名的行（共 {len(pending)} 行，最多列 15）===")
for row in pending[:15]:
    print(f"  行{row[0]}: 店铺={row[1]!r} SPU={row[2]!r} 主图={row[3]!r} 状态={row[4]!r} 标题={row[5]!r}")

wb.close()
