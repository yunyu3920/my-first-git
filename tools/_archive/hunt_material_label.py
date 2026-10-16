"""从店小秘编辑页引的 JS 里找「素材图」的说明文字（只读）。

思路：读编辑页 HTML → 抽出它引的 JS → 在 JS 里搜「素材图」附近的文案。
"""

from __future__ import annotations

import re
import sys

from temu_bot.config import load_config
from temu_bot.dianxiaomi.client import DxmClient

LOGIN = sys.argv[1] if len(sys.argv) > 1 else "lovely"
PRODUCT_ID = "184807703165779045"

cfg = load_config()
with DxmClient(cfg, LOGIN) as client:
    http = client._client
    edit = http.get(f"http://www.dianxiaomi.com/web/popTemu/edit?id={PRODUCT_ID}")
    html = edit.text

    scripts = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html)
    print(f"编辑页引用了 {len(scripts)} 个 JS")
    for s in scripts[:20]:
        print(f"  {s}")

    # 也在页面内联脚本里找
    inline = re.findall(r"<script[^>]*>([\s\S]*?)</script>", html)
    blob = "\n".join(inline)
    print(f"\n内联脚本共 {len(blob)} 字符")

    keywords = ["素材图", "materialImg", "素材"]
    for kw in keywords:
        n = blob.count(kw)
        print(f"  内联脚本里「{kw}」出现 {n} 次")

    # 逐个拉 JS，找「素材图」
    base = "http://www.dianxiaomi.com"
    found = 0
    for s in scripts:
        url = s if s.startswith("http") else base + ("" if s.startswith("/") else "/") + s
        try:
            r = http.get(url)
        except Exception as exc:  # noqa: BLE001
            print(f"  拉不到 {url}: {exc}")
            continue
        js = r.text
        if "素材图" in js or "materialImg" in js:
            found += 1
            print(f"\n★ {url}（{len(js)} 字符）里有")
            for m in list(re.finditer(r"素材图", js))[:6]:
                seg = re.sub(r"\s+", " ", js[max(0, m.start() - 260): m.start() + 160])
                print(f"    …{seg}…\n")
            for m in list(re.finditer(r"materialImg", js))[:4]:
                seg = re.sub(r"\s+", " ", js[max(0, m.start() - 200): m.start() + 140])
                print(f"    [materialImg] …{seg}…\n")
        if found >= 3:
            break

    if not found:
        print("\n这些 JS 里都没有「素材图」——说明它可能在别的页面/别的 chunk 里。")
