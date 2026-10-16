"""找 V3（域名替换）和 U3（i3 里那个必须成功的异步调用）（只读）。"""

from __future__ import annotations

import re
from pathlib import Path

JS = Path(r"D:\temu_bot\data\reports\js_all")

for name in ("useBasicStore-B-TORBDf.js", "index-DyY2dwsZ.js", "index-DCvmsPKi.js"):
    path = JS / name
    if not path.exists():
        continue
    text = path.read_text(encoding="utf-8", errors="replace")
    print(f"\n########## {name} ##########")
    for fn in ("V3", "U3"):
        for pat in (rf"function {fn}\(", rf"const {fn}\s*=", rf"{fn}=async", rf"{fn}\s*=\s*\("):
            hits = list(re.finditer(pat, text))
            if not hits:
                continue
            for m in hits[:1]:
                seg = text[max(0, m.start() - 120): m.start() + 800]
                print(f"\n  ── {fn}（匹配 {pat!r}）──")
                print(f"  {re.sub(r'\\s+', ' ', seg)}")
            break

# 全局找包含 pddpic / dianxiaomi 域名的替换逻辑
print("\n\n########## 全局搜域名替换 ##########")
for path in sorted(JS.glob("*.js")):
    text = path.read_text(encoding="utf-8", errors="replace")
    if "pddpic" not in text:
        continue
    print(f"\n★ {path.name}")
    for m in list(re.finditer(r"pddpic", text))[:3]:
        print(f"  …{re.sub(r'\\s+', ' ', text[max(0, m.start()-450): m.start()+350])}…\n")
