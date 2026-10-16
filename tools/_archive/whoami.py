"""当前登录态到底是谁：打印 shopMap 结构 + 店铺名 + 用模板 SPU 搜一下。"""

from __future__ import annotations

import json
import sys

from temu_bot.config import load_config
from temu_bot.dianxiaomi.client import DxmClient

login = sys.argv[1] if len(sys.argv) > 1 else "hat-tshirt"
spu = sys.argv[2] if len(sys.argv) > 2 else "8909735440"

cfg = load_config()
with DxmClient(cfg, login) as client:
    data = (client.user_in() or {}).get("data") or {}
    print(f"登录态 {login!r}")
    print("userIn.data 顶层键:", sorted(data)[:25])

    shop_map = data.get("shopMap") or {}
    print(f"\nshopMap 平台数: {len(shop_map)}")
    for platform, value in shop_map.items():
        if isinstance(value, dict):
            print(f"  [{platform}] dict keys={sorted(value)[:12]}")
            print(f"      name={value.get('name')!r} shopName={value.get('shopName')!r} id={value.get('id')} idStr={value.get('idStr')!r}")
        elif isinstance(value, list):
            print(f"  [{platform}] list len={len(value)}")
            for item in value[:5]:
                if isinstance(item, dict):
                    print(f"      name={item.get('name')!r} shopName={item.get('shopName')!r} id={item.get('id')} idStr={item.get('idStr')!r}")
                else:
                    print(f"      {item!r}")
        else:
            print(f"  [{platform}] = {value!r}")

    print("\nclient.shops() 归一化结果:")
    for s in client.shops():
        print(f"  id={s.get('id')!r} name={s.get('name')!r} platform={s.get('platform')!r}")

    print(f"\n模板 SPU {spu} 搜索:")
    for state in ("online", "offline"):
        d = client.list_products(dxm_state=state, page_size=5, search_type=3, search_value=spu)
        page = (d or {}).get("page") or {}
        rows = page.get("list") or []
        print(f"  [{state}] totalSize={page.get('totalSize')}")
        for r in rows[:3]:
            print(f"      id={r.get('id')} name={str(r.get('productName'))[:34]!r}")
