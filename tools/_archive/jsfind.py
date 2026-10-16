"""在拉下来的店小秘 JS chunk 里搜关键词，打印有上限的上下文窗口。

minified 的 JS 一行几十万字符，普通 grep 输出会被截断或淹没；这里按窗口打印。

用法:
    python tools\\jsfind.py 关键词1 [关键词2 ...] [--chars 600] [--limit 5] [--dir 目录]
"""

from __future__ import annotations

import sys
from pathlib import Path

DEFAULT_DIR = Path(r"D:\temu_bot\data\reports\js_all")


def main() -> None:
    args = sys.argv[1:]
    chars, limit = 600, 5
    directory = DEFAULT_DIR
    keywords: list[str] = []

    i = 0
    while i < len(args):
        token = args[i]
        if token == "--chars":
            chars = int(args[i + 1]); i += 2
        elif token == "--limit":
            limit = int(args[i + 1]); i += 2
        elif token == "--dir":
            directory = Path(args[i + 1]); i += 2
        else:
            keywords.append(token); i += 1

    if not keywords:
        print(__doc__)
        return

    files = sorted(directory.glob("*.js"))
    print(f"搜索目录 {directory}（{len(files)} 个文件）\n")

    for keyword in keywords:
        print("=" * 30, keyword, "=" * 30)
        total = 0
        for path in files:
            text = path.read_text(encoding="utf-8", errors="replace")
            start = 0
            shown = 0
            while shown < limit:
                index = text.find(keyword, start)
                if index < 0:
                    break
                window_start = max(0, index - chars // 3)
                print(f"\n--- {path.name} @{index} ---")
                print(text[window_start:index + chars].replace("\n", " "))
                start = index + len(keyword)
                shown += 1
                total += 1
        print(f"\n（{keyword} 命中 {total} 处）\n")


if __name__ == "__main__":
    main()
