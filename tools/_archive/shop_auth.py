"""检查店小秘里各店铺的授权状态（access_token / authTime 等，只读）。"""

from __future__ import annotations

import sys
from datetime import datetime

from temu_bot.config import load_config
from temu_bot.dianxiaomi.client import DxmClient

LOGIN = sys.argv[1] if len(sys.argv) > 1 else "lovely"


def ts(v):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return v
    if n > 10**12:  # 毫秒
        n //= 1000
    if n <= 0:
        return v
    return f"{datetime.fromtimestamp(n):%Y-%m-%d %H:%M:%S}"


cfg = load_config()
with DxmClient(cfg, LOGIN) as client:
    data = (client.user_in() or {}).get("data") or {}
    shop_map = data.get("shopMap") or {}

    for key, value in shop_map.items():
        if not isinstance(value, dict):
            continue
        print(f"=== [{key}] {value.get('name')!r} ===")
        for field in (
            "id", "name", "platform", "state", "authTime", "amsAuthTime",
            "vipExpired", "expireTime", "tokenExpireTime", "refreshTokenTimeout",
            "shopSiteList",
        ):
            if field in value:
                val = value.get(field)
                if "Time" in field or field == "vipExpired":
                    print(f"  {field}: {val}  → {ts(val)}")
                else:
                    print(f"  {field}: {str(val)[:120]}")

        tok = value.get("accessToken")
        print(f"  accessToken: {'有(%d字符)' % len(str(tok)) if tok else '无'}")

        # 有没有其它带 token/expire/auth 字样的字段
        extra = [
            k for k in value
            if any(t in k.lower() for t in ("token", "expire", "auth", "reauthor"))
            and k not in ("accessToken", "authTime", "amsAuthTime")
        ]
        for k in sorted(extra):
            print(f"  · {k}: {str(value.get(k))[:100]}")
        print()
