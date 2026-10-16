"""找到 useBasicStore 里的 i3 —— 它把图片变成能塞进 previewImgUrls 的地址（只读）。"""

from __future__ import annotations

import re
from pathlib import Path

JS = Path(r"D:\temu_bot\data\reports\js_all")

for name in ("useBasicStore-B-TORBDf.js",):
    text = (JS / name).read_text(encoding="utf-8", errors="replace")
    print(f"### {name}（{len(text)} 字符）\n")

    for pat in (r"function i3\s*\(", r"\bi3\s*=\s*(?:async\s*)?\(", r"const i3", r"i3=async"):
        for m in list(re.finditer(pat, text))[:2]:
            seg = text[max(0, m.start() - 200): m.start() + 1400]
            print(f"── 匹配 {pat!r} ──")
            print(re.sub(r"\s+", " ", seg))
            print()

    # 也看看有没有别的"上传后拿地址"的封装
    print("### 找 upload / cos 相关的封装函数")
    for kw in ("getSign", "cosDxmCallBack", "uploadImg", "fileToUrl", "toUrl"):
        hits = [m.start() for m in re.finditer(re.escape(kw), text)]
        if hits:
            print(f"  「{kw}」{len(hits)} 次")
            for pos in hits[:1]:
                print(f"    …{re.sub(r'\\s+', ' ', text[max(0, pos-500): pos+500])}…")
