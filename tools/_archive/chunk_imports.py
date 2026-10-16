"""看某个 chunk 导入了什么（尤其从哪个模块导入哪些 API 函数）。

用法: python tools\\chunk_imports.py <chunk文件名> [--head N]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

JS_DIR = Path(r"D:\temu_bot\data\reports\js_all")
IMPORT_RE = re.compile(r"""import\s*\{([^}]{0,600})\}\s*from\s*["']([^"']+)["']""")


def main() -> None:
    name = sys.argv[1]
    head = 0
    if "--head" in sys.argv:
        head = int(sys.argv[sys.argv.index("--head") + 1])

    text = (JS_DIR / name).read_text(encoding="utf-8", errors="replace")
    print(f"文件 {name}  长度 {len(text):,}")
    print("=" * 60)
    if head:
        print(text[:head])

    print("=" * 60)
    for match in IMPORT_RE.finditer(text):
        names = " ".join(match.group(1).split())
        source = match.group(2)
        print(f"\nfrom {source}")
        print(f"   {names[:400]}")


if __name__ == "__main__":
    main()
