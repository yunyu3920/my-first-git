"""核对"新主图排第 1 + 模板展示图保留"这条规矩有没有真的落实（只读）。

对每个商品检查：
  1. 轮播第 1 张 = 这个商品自己上传的主图（不是模板的）
  2. 轮播第 2 张往后 = **模板那几张展示图**，一张不差、顺序一致
  3. 模板原来的主图**不在**轮播里（被删掉了）
  4. SKU 缩略图 = 新主图
  5. **颜色/尺码一个字都没变**

用法：
    python tools\\verify_showcase.py hat-tshirt 220114595067275322 <商品id> [<商品id> ...]
"""

from __future__ import annotations

import json
import sys

from temu_bot.config import load_config
from temu_bot.dianxiaomi.client import DxmClient
from temu_bot.imaging.swap import preview_urls

argv = sys.argv[1:]
if len(argv) < 3:
    raise SystemExit(__doc__)

login, template_id = argv[0], argv[1]
product_ids = argv[2:]

cfg = load_config()
problems: list[str] = []

with DxmClient(cfg, login) as client:
    template = ((client.product_edit(template_id).get("data") or {}).get("product")) or {}
    t_previews = preview_urls(template)
    t_main, t_showcase = t_previews[:1], t_previews[1:]
    print(f"模板 {template_id}：轮播 {len(t_previews)} 张")
    print(f"  主图   {t_main[0] if t_main else '(无)'}")
    for n, u in enumerate(t_showcase):
        print(f"  展示{n + 1}  {u}")
    print()

    for product_id in product_ids:
        product = ((client.product_edit(product_id).get("data") or {}).get("product")) or {}
        if not product:
            problems.append(f"{product_id}：读不到")
            continue

        got = preview_urls(product)
        skus = json.loads(product.get("variationListStr") or "[]")
        thumbs = {e.get("thumbUrl") for e in skus}
        spec_before = {
            json.dumps(e.get("productSkuSpecReqs"), ensure_ascii=False) for e in skus
        }

        print(f"商品 {product_id}｜{product.get('productName')}")
        print(f"  轮播 {len(got)} 张：")
        for n, u in enumerate(got):
            mark = ""
            if n == 0:
                mark = "  ← 新主图" if "dianxiaomi.com" in u else "  ← ⚠ 不是我们传的"
            print(f"    [{n}] {u}{mark}")
        print(f"  缩略图 {thumbs}")

        if len(got) != 1 + len(t_showcase):
            problems.append(f"{product_id}：轮播 {len(got)} 张，应为 {1 + len(t_showcase)} 张")
        if got[:1] and "dianxiaomi.com" not in got[0]:
            problems.append(f"{product_id}：第 1 张不是这次上传的新主图：{got[0]}")
        if got[1:] != t_showcase:
            problems.append(f"{product_id}：展示图与模板对不上\n    实际 {got[1:]}\n    应为 {t_showcase}")
        if t_main and t_main[0] in got:
            problems.append(f"{product_id}：模板的原主图还在轮播里（应该删掉）")
        if thumbs != {got[0]} if got else False:
            problems.append(f"{product_id}：SKU 缩略图不是新主图：{thumbs}")

        # 颜色/尺码
        t_skus = json.loads(template.get("variationListStr") or "[]")
        t_specs = [e.get("productSkuSpecReqs") for e in t_skus]
        g_specs = [e.get("productSkuSpecReqs") for e in skus]
        if g_specs != t_specs:
            problems.append(f"{product_id}：颜色/尺码规格被改动了！\n    现在 {g_specs}\n    模板 {t_specs}")
        print()

print("=" * 74)
if problems:
    print(f"❌ {len(problems)} 处不对：")
    for p in problems:
        print(f"   · {p}")
    raise SystemExit(1)
print("✅ 全部通过：新主图第 1、模板 4 张展示图保留、原主图已删、颜色未动")
