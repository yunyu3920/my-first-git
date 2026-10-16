"""读一个店小秘商品 id 的详情（只读），看它现在是什么状态、图是什么。"""

from __future__ import annotations

import json
import sys

from temu_bot.config import load_config
from temu_bot.dianxiaomi.client import DxmClient
from temu_bot.product import get_en_title

PRODUCT_ID = sys.argv[1] if len(sys.argv) > 1 else "184807703165779045"
LOGIN = sys.argv[2] if len(sys.argv) > 2 else "lovely"

cfg = load_config()
with DxmClient(cfg, LOGIN) as client:
    resp = client.product_edit(PRODUCT_ID)
    product = (resp.get("data") or {}).get("product") or {}
    if not product:
        print(f"❌ 读不到商品 {PRODUCT_ID}（data 为空）")
        print("   原始返回片段:", json.dumps(resp, ensure_ascii=False)[:400])
        raise SystemExit(0)

    print(f"=== 商品 {PRODUCT_ID} ===")
    for key in (
        "id", "productId", "productName", "dxmState", "categoryId", "categoryType",
        "shopId", "materialImgUrl", "outerGoodsUrl", "sourceUrl", "originFileUrl",
    ):
        print(f"  {key}: {product.get(key)!r}")

    print(f"  英文标题: {get_en_title(product)!r}")

    # 图片相关字段全列出来
    print("\n--- 所有含 img/image/url 的字段 ---")
    for k, v in sorted(product.items()):
        kl = k.lower()
        if any(t in kl for t in ("img", "image", "url", "pic", "photo")):
            print(f"  {k}: {str(v)[:220]}")

    var = product.get("variationListStr")
    print("\n--- variationListStr ---")
    if isinstance(var, str):
        try:
            var = json.loads(var)
        except Exception:  # noqa: BLE001
            pass
    if isinstance(var, list):
        for i, item in enumerate(var[:3]):
            if isinstance(item, dict):
                print(f"  变体[{i}] keys={sorted(item)[:20]}")
                for k, v in item.items():
                    if any(t in k.lower() for t in ("img", "image", "url", "pic", "sku")):
                        print(f"      {k}: {str(v)[:200]}")
    else:
        print(f"  {str(var)[:300]}")
