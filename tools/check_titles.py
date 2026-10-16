"""查表里的英文标题合不合 Temu 的规矩（只读）。

Temu 的报错原话：`英文名称不可输入非英文字母或表情符号`。
这个店历史上 195 条发布失败就是这么来的，所以发之前先查一遍，别等平台打回。

判定规则（对着真实报错和平台报错文案来的）：
  - 只允许 ASCII 可打印字符（A-Z a-z 0-9 空格和常见标点）
  - 不允许中文、日文、韩文、全角标点、emoji
  - 不允许连续空格、首尾空格
  - 长度上限（Temu 英文名一般 20~150 字符，这里只做宽上限告警）

用法：
    python tools\\check_titles.py            # 查配置里的当前表
    python tools\\check_titles.py <表路径>
"""

from __future__ import annotations

import sys
import unicodedata
from pathlib import Path

from temu_bot.config import load_config
from temu_bot.sheet.table import SheetTable

EN_MAX = 150


def problems_with_en(title: str) -> list[str]:
    issues: list[str] = []
    if not title:
        return ["（空）"]
    bad = {ch for ch in title if not (ch.isascii() and ch.isprintable())}
    if bad:
        shown = "".join(sorted(bad))
        names = "、".join(unicodedata.name(ch, f"U+{ord(ch):04X}") for ch in sorted(bad))
        issues.append(f"含非 ASCII 字符「{shown}」（{names}）")
    if title != title.strip():
        issues.append("首尾有空格")
    if "  " in title:
        issues.append("有连续空格")
    if len(title) > EN_MAX:
        issues.append(f"太长（{len(title)} > {EN_MAX}）")
    return issues


table_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
cfg = load_config()
rows = list(SheetTable.from_config(cfg).iter_rows()) if table_path is None else []
if table_path is not None:
    from temu_bot.sheet.table import SheetTable as ST

    rows = list(ST(table_path, cfg.project.sheet_name, cfg.sheet).iter_rows())

print(f"表：{table_path or cfg.project.table}｜{len(rows)} 行\n")

bad = 0
for row in rows:
    issues = problems_with_en(row.title_en)
    marked = "❌" if issues else "✅"
    if issues:
        bad += 1
    print(f"{marked} 第 {row.row_index} 行")
    print(f"     EN: {row.title_en or '(空)'}")
    for issue in issues:
        print(f"     → {issue}")

print(f"\n共 {len(rows)} 行，英文标题有问题 {bad} 行")
raise SystemExit(1 if bad else 0)
