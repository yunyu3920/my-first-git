"""检查一行的主图/素材图尺寸、比例、体积是否满足 Temu 要求（只读）。

Temu 半托管主图要求（来自第 32 行的接口报错原文）：
  宽高比 3:4、宽 >= 1340px、高 >= 1785px、体积 <= 2MB
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

FOLDER = Path(r"C:\Users\A\Desktop\Temu")

REQ_W, REQ_H, REQ_MAX_BYTES = 1340, 1785, 2 * 1024 * 1024


def check(path: Path) -> None:
    if not path.exists():
        print(f"  ❌ 不存在：{path.name}")
        return
    size = path.stat().st_size
    with Image.open(path) as im:
        w, h = im.size
        fmt, mode = im.format, im.mode

    ratio_ok = abs(w / h - 3 / 4) < 0.01
    size_ok = w >= REQ_W and h >= REQ_H
    bytes_ok = size <= REQ_MAX_BYTES

    print(f"  {path.name}")
    print(f"    尺寸 {w}x{h}  比例 {w/h:.4f}（要 0.75）{'✅' if ratio_ok else '❌'}")
    print(f"    阈值 宽>={REQ_W} 高>={REQ_H} {'✅' if size_ok else '❌'}")
    print(f"    体积 {size/1024:.1f} KB / 上限 2048 KB {'✅' if bytes_ok else '❌'}")
    print(f"    格式 {fmt}  模式 {mode}")

    if not (ratio_ok and size_ok and bytes_ok):
        print("    → 需要处理（裁剪/放大到 1340x1785 以内且比例 3:4）")


names = sys.argv[1:] or ["26746907929.jpg", "26746907929-素材图.jpg"]
print(f"检查目录：{FOLDER}\n")
for name in names:
    check(FOLDER / name)
    print()
