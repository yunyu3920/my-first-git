"""商品图的文件名像不像文件内容的 MD5？（只读）

商品 184807703165779045 的图：
    素材图  https://img.cdnfe.com/product/open/cb8f24e3908c46b5acbfc4914d1a1f9b-goods.jpeg
    轮播图  https://img.cdnfe.com/product/open/478da6f98b874759ac3de00ec4c99bb9-goods.jpeg
    ...
拿本地素材文件算各种哈希对一下。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

LOCAL = Path(r"C:\Users\A\Desktop\Temu")
TARGETS = {
    "素材图": "cb8f24e3908c46b5acbfc4914d1a1f9b",
    "轮播1": "478da6f98b874759ac3de00ec4c99bb9",
    "轮播2": "242d8dd0058a4328bf4b683690a8e090",
    "轮播3": "9a01223488b54a2b9ff5520a2be13b33",
    "轮播4": "46ca690f75b24a54b7c3856ff3d032ab",
}

print("=== 目标哈希（来自商品图地址）===")
for k, v in TARGETS.items():
    print(f"  {k}: {v}")

print("\n=== 本地候选文件 ===")
candidates = sorted(LOCAL.glob("*.jpg"))[:6]
for p in candidates:
    data = p.read_bytes()
    hashes = {
        "md5": hashlib.md5(data).hexdigest(),
        "sha256-32": hashlib.sha256(data).hexdigest()[:32],
        "sha1-32": hashlib.sha1(data).hexdigest()[:32],
    }
    print(f"\n  {p.name}（{len(data)} 字节）")
    for name, h in hashes.items():
        hit = [k for k, v in TARGETS.items() if v == h]
        mark = f"   ★★★ 命中 {hit}！" if hit else ""
        print(f"    {name:10} {h}{mark}")

    # 也试试"上传后 COS 返回的 url 里那段"
    print(f"    （上传返回的 url 里文件名是随机的，不是内容哈希）")
