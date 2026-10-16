"""看商品列表返回里有没有 shopId，以及 shopId 过滤参数好不好使（只读）。"""

from __future__ import annotations

import json
import sys

from temu_bot.config import load_config
from temu_bot.dianxiaomi.client import DxmClient

login = sys.argv[1] if len(sys.argv) > 1 else "hat-tshirt"
spu = sys.argv[2] if len(sys.argv) > 2 else "9082676134"

cfg = load_config()
with DxmClient(cfg, login) as client:
    print("=== 账号里的店铺 ===")
    shops = client.shops()
    for s in shops:
        print(f"  {json.dumps(s, ensure_ascii=False)[:200]}")

    print(f"\n=== 全店搜 SPU {spu}（shopId=-1）===")
    data = client.list_products(
        dxm_state="online", page_size=5, search_type=3, search_value=spu
    )
    rows = ((data.get("page") or {}).get("list")) or []
    print(f"  命中 {len(rows)} 条")
    for row in rows[:3]:
        keep = {
            k: v
            for k, v in row.items()
            if any(t in k.lower() for t in ("id", "name", "shop", "state", "spu"))
        }
        print(f"    {json.dumps(keep, ensure_ascii=False)[:300]}")

    if shops:
        sid = str(shops[0].get("id"))
        print(f"\n=== 限定 shopId={sid} 再搜 ===")
        data = client.list_products(
            dxm_state="online", page_size=5, search_type=3, search_value=spu, shop_id=sid
        )
        rows = ((data.get("page") or {}).get("list")) or []
        print(f"  命中 {len(rows)} 条")
        for row in rows[:3]:
            print(f"    shopId={row.get('shopId')}  {str(row.get('productName'))[:30]}")

        print(f"\n=== 限定一个不存在的 shopId=999999 再搜（应命中 0）===")
        data = client.list_products(
            dxm_state="online", page_size=5, search_type=3, search_value=spu, shop_id="999999"
        )
        rows = ((data.get("page") or {}).get("list")) or []
        print(f"  命中 {len(rows)} 条")
