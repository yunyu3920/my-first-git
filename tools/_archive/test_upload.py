"""真的上传一张图片，验证三步流程通不通（会上传到你的店小秘图片空间）。

默认传你自己的素材图，不凭空造垃圾文件。
"""

from __future__ import annotations

import sys
from pathlib import Path

from temu_bot.config import load_config
from temu_bot.dianxiaomi.client import DxmClient

LOGIN = sys.argv[1] if len(sys.argv) > 1 else "lovely"
IMAGE = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(r"C:\Users\A\Desktop\Temu\00742093597-素材图.jpg")

print(f"登录态 {LOGIN}")
print(f"要传的图 {IMAGE}（{IMAGE.stat().st_size if IMAGE.exists() else '?'} 字节）\n")

cfg = load_config()
with DxmClient(cfg, LOGIN) as client:
    print("三步走：取签名 → PUT 到对象存储 → 回调登记\n")

    signed = client.cos_get_sign_for(IMAGE.name)
    print("① 签名")
    print(f"    fileId = {signed.get('fileId')}")
    print(f"    url    = {signed.get('url')}")

    content = IMAGE.read_bytes()
    import httpx

    from temu_bot.dianxiaomi.client import _guess_image_mime

    target = client.cos_put(
        signed["sign"], signed["url"], content, _guess_image_mime(IMAGE.suffix)
    )
    print(f"\n② PUT 成功 → {target}")

    result = client.cos_register(
        file_id=signed.get("fileId", ""), file_name=IMAGE.name, file_size=len(content)
    )
    print("\n③ 登记返回")
    for k, v in (result or {}).items():
        print(f"    {k} = {str(v)[:200]}")

    # 这个地址能不能访问？
    print("\n=== 用返回的地址取一下，看能不能访问 ===")
    for key in ("url", "imgUrl", "fileUrl", "path"):
        u = result.get(key)
        if not u:
            continue
        full = u if str(u).startswith("http") else "https:" + str(u)
        try:
            r = httpx.get(full, timeout=30, follow_redirects=True)
            print(f"    {key}: HTTP {r.status_code}，{len(r.content)} 字节 → {full}")
        except Exception as exc:  # noqa: BLE001
            print(f"    {key}: 取不到（{exc}）→ {full}")
