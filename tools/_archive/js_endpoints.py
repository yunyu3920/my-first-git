"""列出指定 JS 文件（或最近新增的）里出现的所有 /api/*.json 接口。

用法:
    python tools\\js_endpoints.py                # 最近 20 分钟新增的 chunk
    python tools\\js_endpoints.py 文件名1 文件名2   # 指定文件
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

JS_DIR = Path(r"D:\temu_bot\data\reports\js_all")
API_RE = re.compile(r"""["'`](/api/[A-Za-z0-9_/\-\.\$\{\}]{3,80}\.json)["'`]""")


def main() -> None:
    names = sys.argv[1:]
    if names:
        files = [JS_DIR / n for n in names]
    else:
        cutoff = time.time() - 1800
        files = [p for p in sorted(JS_DIR.glob("*.js")) if p.stat().st_mtime > cutoff]

    print(f"检查 {len(files)} 个文件：")
    for p in files:
        print("  ", p.name)

    for path in files:
        if not path.exists():
            print(f"\n!! 不存在 {path.name}")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        eps = sorted(set(API_RE.findall(text)))
        print(f"\n=== {path.name}（{len(text):,} 字符）共 {len(eps)} 个接口 ===")
        for e in eps[:40]:
            print("   ", e)


if __name__ == "__main__":
    main()
