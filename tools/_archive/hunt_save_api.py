"""找"保存商品"接口：搜动态拼接的 API 路径。

店小秘前端不少接口路径是拼出来的，字面量搜索会漏。
"""

from __future__ import annotations

import re
from pathlib import Path

JS_DIR = Path(r"D:\temu_bot\data\reports\js_all")

PATTERNS: dict[str, str] = {
    "popTemuProduct/ 作为前缀（后面拼接）": r"[\"'\u0060]/api/popTemuProduct/[\"'\u0060]\s*\+",
    "popTemuProduct/ 后跟变量": r"/api/popTemuProduct/\$\{?",
    "importProduct": r"importProduct\.json",
    "模板字符串形式的 api 路径": r"\u0060/api/[A-Za-z0-9_/\-\.]*\$\{",
    "saveOrUpdate / saveEdit / editProduct": r"/(?:saveOrUpdate|saveEdit|editProduct|modifyProduct|updateProduct)[A-Za-z0-9_\-\.]*\.json",
    "add 后缀带 hash 的接口": r"/api/[A-Za-z0-9_/\-\.]*add[0-9a-f]{8}\.json",
}


def main() -> None:
    files = sorted(JS_DIR.glob("*.js"))
    print(f"扫描 {len(files)} 个 JS\n")
    for label, pattern in PATTERNS.items():
        print("=" * 20, label, "=" * 20)
        rx = re.compile(pattern)
        hits = 0
        for path in files:
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in rx.finditer(text):
                start = max(0, match.start() - 130)
                snippet = text[start:match.start() + 220].replace("\n", " ")
                print(f"  {path.name}: …{snippet}")
                hits += 1
                if hits >= 3:
                    break
            if hits >= 3:
                break
        if hits == 0:
            print("  (未命中)")
        print()


if __name__ == "__main__":
    main()
