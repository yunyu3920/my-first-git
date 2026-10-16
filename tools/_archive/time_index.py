"""量一下目录索引的耗时——尤其"选错大目录"时会多慢。"""

from __future__ import annotations

import time
from pathlib import Path

from temu_bot.collect import CollectOptions, check_main_images, scan_folder
from temu_bot.imaging.resolve import index_folder

CASES = [
    Path(r"C:\Users\A\Desktop\Temu"),          # 正常素材目录（198 张图）
    Path(r"C:\Users\A\Desktop\图片批处理工具箱"),  # 带 node_modules 的小项目
    Path(r"C:\Users\A\Desktop"),               # 整个桌面（选错目录的典型）
]


def timed(label, fn):
    t0 = time.perf_counter()
    out = fn()
    dt = (time.perf_counter() - t0) * 1000
    print(f"  {label:36} {dt:9.0f} ms")
    return out, dt


for recursive in (False, True):
    mode = "递归（含子文件夹）" if recursive else "只看这一层（默认）"
    print(f"\n########## 收集类型：{mode} ##########")
    for folder in CASES:
        print(f"\n=== {folder} ===")
        if not folder.is_dir():
            print("  (不存在)")
            continue
        index, t_index = timed(f"index_folder(recursive={recursive})",
                               lambda f=folder, r=recursive: index_folder(f, recursive=r))
        print(f"      → 找到 {len(index.files)} 个图片文件")

        opts = CollectOptions(folder=folder, shop="x", template_spu="y", recursive=recursive)
        scan, t_scan = timed("scan_folder", lambda o=opts: scan_folder(o))
        _, t_spec = timed("check_main_images（复用索引，不重扫）",
                          lambda s=scan: check_main_images(s))
        print(f"      → 收成 {len(scan.rows)} 行")
        print(f"      ★ 端点等价耗时 ≈ {t_scan + t_spec:.0f} ms")
