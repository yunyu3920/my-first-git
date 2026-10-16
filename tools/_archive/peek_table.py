"""快速看一眼表格前几行的关键列（只读，不写回）。"""

from __future__ import annotations

import sys

import openpyxl

PATH = r"C:\Users\A\Desktop\Temu\商品信息表.xlsx"
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 12

wb = openpyxl.load_workbook(PATH, read_only=True, data_only=True)
ws = wb["Sheet1"]
rows = list(ws.iter_rows(min_row=1, max_row=LIMIT + 1, values_only=True))

print("列数:", len(rows[0]))
for i, h in enumerate(rows[0], 1):
    print(f"  {i:2d}. {h}")

print("\n=== 各行关键列 ===")


def cell(row, n):
    v = row[n - 1] if len(row) >= n else None
    return "" if v is None else str(v).replace("\n", "/").replace("\r", "")


for idx, r in enumerate(rows[1:], start=2):
    print(
        f"\n行{idx}: 店铺={cell(r,10)!r}  SPU={cell(r,11)!r}  主图={cell(r,2)!r}"
        f"  状态={cell(r,14)!r}  商品ID={cell(r,13)!r}"
    )
    print(f"      文件夹={cell(r,1)}")
    print(f"      标题CN={cell(r,17)[:40]!r}")
    print(f"      标题EN={cell(r,18)[:60]!r}")

wb.close()
