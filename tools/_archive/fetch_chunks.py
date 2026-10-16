"""按文件名直接从静态资源服务器取 chunk，并列出里面的 /api/ 接口。

用法: python tools\\fetch_chunks.py 文件名1 [文件名2 ...]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import httpx

ASSET_BASE = "https://s13.dianxiaomi.com/dxm-web/2026-10/assets"
OUT_DIR = Path(r"D:\temu_bot\data\reports\js_all")
API_RE = re.compile(r"""["'`](/api/[A-Za-z0-9_/\-\.]+\.json)["'`]""")


def main() -> None:
    names = sys.argv[1:]
    if not names:
        print(__doc__)
        return

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        for name in names:
            target = OUT_DIR / name
            if target.exists():
                text = target.read_text(encoding="utf-8", errors="replace")
            else:
                response = client.get(f"{ASSET_BASE}/{name}")
                if response.status_code != 200:
                    print(f"!! {name} 下载失败 HTTP {response.status_code}")
                    continue
                text = response.text
                target.write_text(text, encoding="utf-8")

            endpoints = sorted(set(API_RE.findall(text)))
            print(f"\n=== {name}  ({len(text):,} 字符)  接口 {len(endpoints)} 个 ===")
            for endpoint in endpoints:
                print("   ", endpoint)


if __name__ == "__main__":
    main()
