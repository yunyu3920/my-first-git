"""找 getSign / cosDxmCallBack 的调用点，把参数名挖出来（只读）。

接口定义已经拿到：
    BD(e) → /api/cos/getSign.json
    zD(e) → /api/cos/cosDxmCallBack.json
但要知道各处传的是什么字段。
"""

from __future__ import annotations

import re
from pathlib import Path

JS = Path(r"D:\temu_bot\data\reports\js_all")
TARGET = JS / "index-DyY2dwsZ.js"

text = TARGET.read_text(encoding="utf-8", errors="replace")
print(f"文件 {TARGET.name}，{len(text)} 字符\n")

# 先定位定义，确认压缩名没变
for name, api in (("BD", "getSign"), ("zD", "cosDxmCallBack")):
    m = re.search(rf"function {name}\(e\)\{{return u\.postForm\(\"/api/cos/{api}\.json\",e\)\}}", text)
    print(f"定义 {name} → {api}: {'✅ 找到' if m else '❌ 没找到'}")

# 找调用点：BD(...) / zD(...)，排除定义本身
print("\n=== 调用点 ===")
for name in ("BD", "zD"):
    print(f"\n──────── {name}( 的调用 ────────")
    count = 0
    for m in re.finditer(rf"[^A-Za-z0-9_$]{name}\(", text):
        seg = text[max(0, m.start() - 1500): m.start() + 1500]
        # 跳过定义处
        if f"function {name}(e)" in seg:
            continue
        count += 1
        print(f"\n--- 第 {count} 处 ---")
        print(re.sub(r"\s+", " ", seg))
        if count >= 3:
            break
    if count == 0:
        print("  （没找到调用点）")

# 顺便看看 COS 的 PUT 是怎么发的（找 putObject / fetch 到 myqcloud）
print("\n\n=== COS 上传（PUT 到对象存储）相关 ===")
for kw in ("putObject", "uploadFile", "put(", "PUT"):
    hits = [m.start() for m in re.finditer(re.escape(kw), text)]
    if not hits:
        continue
    print(f"\n「{kw}」出现 {len(hits)} 次，取前 2 处：")
    for pos in hits[:2]:
        print(f"  …{re.sub(r'\\s+', ' ', text[max(0, pos - 700): pos + 700])}…\n")
