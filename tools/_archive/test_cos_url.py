"""验证上传后的地址能不能直接用（只读）。"""

from __future__ import annotations

import io
import json
import sys

import httpx
from PIL import Image

from temu_bot.config import load_config
from temu_bot.dianxiaomi.client import DxmClient

LOGIN = sys.argv[1] if len(sys.argv) > 1 else "lovely"
COS_URL = "https://wxalbum-10001658-cos.dianxiaomi.com/wxalbum/2668514/20261016122107/bf0ead2791f11c09874643a58a8ee25a.jpg"

print("=== ① 刚才上传的 COS 地址能不能公开访问？ ===")
try:
    r = httpx.get(COS_URL, timeout=30, follow_redirects=True)
    print(f"  HTTP {r.status_code}，{len(r.content)} 字节")
    if r.status_code == 200:
        with Image.open(io.BytesIO(r.content)) as im:
            print(f"  尺寸 {im.size}，格式 {im.format}")
        print("  ✅ 公开可读")
    else:
        print(f"  ❌ 不是公开可读：{r.text[:200]}")
except Exception as exc:  # noqa: BLE001
    print(f"  ❌ 取不到：{exc}")

cfg = load_config()
with DxmClient(cfg, LOGIN) as client:
    print("\n=== ② 图片空间里显示的地址是什么格式？ ===")
    try:
        data = client.album_index(pageNo=1, pageSize=10)
    except Exception as exc:  # noqa: BLE001
        print(f"  调用失败：{exc}")
        raise SystemExit(0)

    payload = (data or {}).get("data") or {}
    print(f"  顶层键：{sorted(payload)[:15]}")
    items = payload.get("list") or payload.get("picList") or payload.get("rows") or []
    print(f"  条目数：{len(items)}")
    for it in items[:6]:
        if isinstance(it, dict):
            url = it.get("url") or it.get("picUrl") or it.get("imgUrl") or ""
            print(f"    {str(it.get('name') or it.get('fileName'))[:30]:32} {str(url)[:100]}")
    if not items:
        print("  原始片段：", json.dumps(payload, ensure_ascii=False)[:500])
