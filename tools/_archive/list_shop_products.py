"""列某登录态下的商品（少量），看有哪些可用作模板 SPU。"""

from __future__ import annotations

import sys

from temu_bot.config import load_config
from temu_bot.dianxiaomi.client import DxmClient

login = sys.argv[1] if len(sys.argv) > 1 else "hat-tshirt"

cfg = load_config()
with DxmClient(cfg, login) as client:
    for state in ("online", "offline", "draft"):
        try:
            d = client.list_products(dxm_state=state, page_size=8)
        except Exception as exc:  # noqa: BLE001
            print(f"[{state}] 异常：{str(exc)[:120]}")
            continue
        page = (d or {}).get("page") or {}
        rows = page.get("list") or []
        print(f"\n=== {state} === totalSize={page.get('totalSize')}")
        for r in rows:
            print(
                f"  id={r.get('id')} spu={r.get('productId')!r}"
                f"  state={r.get('dxmState')!r}  name={str(r.get('productName'))[:36]!r}"
            )
