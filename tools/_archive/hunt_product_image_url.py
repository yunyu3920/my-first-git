"""看商品编辑页拿到图片后，往 previewImgUrls 里塞的是什么地址（只读）。

关键疑问：
  图片空间上传得到的是 COS 地址（wxalbum-10001658-cos.dianxiaomi.com/...），
  但商品里现存的是 img.cdnfe.com/product/open/xxx-goods.jpeg。
  两者是不是一回事？商品编辑页上传后到底用哪个？
"""

from __future__ import annotations

import re
from pathlib import Path

JS = Path(r"D:\temu_bot\data\reports\js_all")

for name in ("index-DCvmsPKi.js", "useBasicStore-B-TORBDf.js"):
    path = JS / name
    if not path.exists():
        print(f"(缺 {name})")
        continue
    text = path.read_text(encoding="utf-8", errors="replace")
    print(f"\n########## {name}（{len(text)} 字符）##########")

    # 1) 用到 previewImgUrls 的地方
    for m in list(re.finditer(r"previewImgUrls", text))[:6]:
        seg = re.sub(r"\s+", " ", text[max(0, m.start() - 450): m.start() + 260])
        print(f"\n  ── previewImgUrls ──\n  …{seg}…")

    # 2) 有没有 cdnfe / goods.jpeg 这类商品图地址的拼装
    for kw in ("cdnfe", "goods.jpeg", "-goods", "open/", "product/open"):
        hits = [m.start() for m in re.finditer(re.escape(kw), text)]
        if hits:
            print(f"\n  ── 「{kw}」出现 {len(hits)} 次，取 2 处 ──")
            for pos in hits[:2]:
                seg = re.sub(r"\s+", " ", text[max(0, pos - 400): pos + 300])
                print(f"  …{seg}…")
