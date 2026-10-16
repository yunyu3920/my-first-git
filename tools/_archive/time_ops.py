"""量一下几个操作的耗时，决定要不要加进度反馈。"""

from __future__ import annotations

import time
from pathlib import Path

from temu_bot.collect import CollectOptions, scan_folder, write_table

FOLDER = Path(r"C:\Users\A\Desktop\Temu")


def timed(label, fn):
    t0 = time.perf_counter()
    out = fn()
    dt = (time.perf_counter() - t0) * 1000
    print(f"{label:28} {dt:8.0f} ms")
    return out


opts = CollectOptions(folder=FOLDER, shop="A lovely costume", template_spu="8909735440")

scan = timed("收集：扫描文件夹", lambda: scan_folder(opts))
print(f"    → 图片 {scan.image_count} 张，收成 {len(scan.rows)} 行")
timed("收集：再扫一次(无缓存)", lambda: scan_folder(opts))

out = write_table(scan, FOLDER / "_timing_test.xlsx")
timed("收集：写表(不含扫描)", lambda: write_table(scan, FOLDER / "_timing_test2.xlsx"))
print(f"    → {out.rows} 行")

# 清理
for p in (FOLDER / "_timing_test.xlsx", FOLDER / "_timing_test2.xlsx"):
    if p.exists():
        p.unlink()
print("已清理临时文件")
