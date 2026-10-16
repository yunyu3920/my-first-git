"""找到 BD/zD/jD 的导出名，再找哪个 chunk 引用了它们（只读）。"""

from __future__ import annotations

import re
from pathlib import Path

JS = Path(r"D:\temu_bot\data\reports\js_all")
DEF_FILE = JS / "index-DyY2dwsZ.js"

text = DEF_FILE.read_text(encoding="utf-8", errors="replace")

# Vite 产物末尾一般是 export{a as b, c as d, ...}
exports = re.findall(r"export\s*\{([^}]*)\}", text)
mapping: dict[str, str] = {}
for block in exports:
    for item in block.split(","):
        item = item.strip()
        m = re.match(r"([A-Za-z0-9_$]+)\s+as\s+([A-Za-z0-9_$]+)$", item)
        if m:
            mapping[m.group(1)] = m.group(2)
        elif re.match(r"^[A-Za-z0-9_$]+$", item):
            mapping[item] = item

print("=== 目标函数在定义文件里的导出名 ===")
for local in ("BD", "zD", "jD", "UD"):
    print(f"  {local} → {mapping.get(local, '(没导出/未找到)')}")

targets = {mapping.get(x) for x in ("BD", "zD") if mapping.get(x)}
print(f"\n要去别的 chunk 里找这些名字：{targets}\n")

for path in sorted(JS.glob("*.js")):
    if path == DEF_FILE:
        continue
    t = path.read_text(encoding="utf-8", errors="replace")
    for name in targets:
        if re.search(rf"\b{re.escape(name)}\b", t):
            print(f"★ {path.name} 里出现「{name}」")
            for m in list(re.finditer(rf"\b{re.escape(name)}\b", t))[:3]:
                seg = re.sub(r"\s+", " ", t[max(0, m.start() - 600): m.start() + 600])
                print(f"    …{seg}…\n")
