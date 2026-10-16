"""找到从主 API 模块里导入 getSign(er) / cosDxmCallBack(eq) 的那个 chunk（只读）。

导出名：
    BD(getSign)        → er
    zD(cosDxmCallBack) → eq
"""

from __future__ import annotations

import re
from pathlib import Path

JS = Path(r"D:\temu_bot\data\reports\js_all")
DEF_FILE = "index-DyY2dwsZ.js"
WANT = {"er", "eq"}

IMPORT_RE = re.compile(r'import\s*\{([^}]*)\}\s*from\s*["\'][^"\']*' + re.escape(DEF_FILE) + r'["\']')

for path in sorted(JS.glob("*.js")):
    if path.name == DEF_FILE:
        continue
    text = path.read_text(encoding="utf-8", errors="replace")
    for m in IMPORT_RE.finditer(text):
        names = [x.strip() for x in m.group(1).split(",") if x.strip()]
        found = {}
        for item in names:
            mm = re.match(r"([A-Za-z0-9_$]+)(?:\s+as\s+([A-Za-z0-9_$]+))?$", item)
            if not mm:
                continue
            orig, alias = mm.group(1), mm.group(2) or mm.group(1)
            if orig in WANT:
                found[orig] = alias
        if found:
            print(f"★ {path.name}")
            print(f"    导入：{found}")
            for orig, alias in found.items():
                for mm in list(re.finditer(rf"[^A-Za-z0-9_$]{re.escape(alias)}\(", text))[:4]:
                    seg = re.sub(r"\s+", " ", text[max(0, mm.start() - 900): mm.start() + 900])
                    print(f"\n    ── 调用 {alias}() 处（对应 {orig}）──")
                    print(f"    …{seg}…")
            print()
