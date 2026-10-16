"""从店小秘前端 JS 里挖出「图片上传」的完整流程（只读）。

要找的三步：
    /api/cos/getSign.json         取对象存储签名
    PUT  到 COS                   把文件传上去
    /api/cos/cosDxmCallBack.json  回调登记，换回店小秘的图片地址

重点是**参数名**——之前只有端点没有载荷。
"""

from __future__ import annotations

import re
from pathlib import Path

JS_DIR = Path(r"D:\temu_bot\data\reports\js_all")
KEYWORDS = ["getSign", "cosDxmCallBack", "toUploadPicFromNet", "cos/", "myqcloud", "putObject"]


def context(text: str, needle: str, before: int = 500, after: int = 900, limit: int = 4) -> list[str]:
    out = []
    for m in list(re.finditer(re.escape(needle), text))[:limit]:
        seg = text[max(0, m.start() - before): m.start() + after]
        out.append(re.sub(r"\s+", " ", seg))
    return out


for path in sorted(JS_DIR.glob("*.js")):
    text = path.read_text(encoding="utf-8", errors="replace")
    hits = [k for k in KEYWORDS if k in text]
    if not hits:
        continue
    print(f"\n########## {path.name}（{len(text)} 字符）命中：{hits} ##########")
    for kw in hits:
        for seg in context(text, kw):
            print(f"\n  ── 关于「{kw}」──")
            print(f"  …{seg}…")
