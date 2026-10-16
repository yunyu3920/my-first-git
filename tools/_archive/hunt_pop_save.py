"""专门搜半托管 Temu 的"保存商品"接口：popTemuProduct 命名空间下带 add/save 的拼法。"""

from __future__ import annotations

import re
from pathlib import Path

JS_DIR = Path(r"D:\temu_bot\data\reports\js_all")

PATTERNS = {
    "popTemuProduct/add 前缀": r"popTemuProduct/add",
    "popTemuProduct 下任意动作": r"/api/popTemuProduct/[A-Za-z0-9_\-]+",
    "字符串 + .json 的拼接": r"[\"'](?:/api/[A-Za-z0-9_/\-]*)?[\"']\s*\+\s*[A-Za-z_$][\w$]*\s*\+\s*[\"']\.json[\"']",
    "pddkjProduct/add": r"pddkjProduct/add",
    "3f8c2a97 作为独立常量": r"[\"']3f8c2a97[\"']",
}


def main() -> None:
    files = sorted(JS_DIR.glob("*.js"))
    for label, pattern in PATTERNS.items():
        print("=" * 18, label, "=" * 18)
        rx = re.compile(pattern)
        hits: set[str] = set()
        shown = 0
        for path in files:
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in rx.finditer(text):
                snippet = match.group(0)
                if snippet in hits:
                    continue
                hits.add(snippet)
                if shown < 12:
                    start = max(0, match.start() - 100)
                    print(f"  {path.name}: …{text[start:match.start() + 160]}".replace("\n", " "))
                    shown += 1
        if not hits:
            print("  (未命中)")
        else:
            print(f"  共 {len(hits)} 种：{sorted(hits)[:14]}")
        print()


if __name__ == "__main__":
    main()
