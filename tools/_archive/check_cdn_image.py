"""下载店小秘 CDN 上的图，量一下尺寸/体积是否满足 Temu 主图要求（只读）。"""

from __future__ import annotations

import io
import sys

import httpx
from PIL import Image

URLS = sys.argv[1:] or [
    "https://img.cdnfe.com/product/open/478da6f98b874759ac3de00ec4c99bb9-goods.jpeg",
    "https://img.cdnfe.com/product/open/cb8f24e3908c46b5acbfc4914d1a1f9b-goods.jpeg",
]

with httpx.Client(timeout=30, follow_redirects=True) as client:
    for url in URLS:
        try:
            r = client.get(url)
        except Exception as exc:  # noqa: BLE001
            print(f"❌ 下载失败 {url[-40:]}: {exc}")
            continue
        nbytes = len(r.content)
        print(f"{url[-46:]}")
        print(f"  HTTP {r.status_code}  体积 {nbytes/1024:.1f} KB")
        if r.status_code != 200:
            continue
        try:
            with Image.open(io.BytesIO(r.content)) as im:
                w, h = im.size
                print(f"  尺寸 {w}x{h}  比例 {w/h:.4f}")
                ok = (
                    abs(w / h - 0.75) < 0.01
                    and w >= 1340
                    and h >= 1785
                    and nbytes <= 2 * 1024 * 1024
                )
                print(f"  {'✅ 合规' if ok else '❌ 不合规'}")
        except Exception as exc:  # noqa: BLE001
            print(f"  解不开：{exc}")
        print()
