"""把米豆余额的**原始返回**打出来（只看，不改）。

用来验证"复制到底扣不扣米豆"这类问题——CLI 的表格只显示了 viewMap 里的几个字段，
原始返回里可能还有别的桶。
"""

from __future__ import annotations

import json
import sys

from temu_bot.config import load_config
from temu_bot.dianxiaomi.client import DxmClient

login = sys.argv[1] if len(sys.argv) > 1 else "hat-tshirt"

cfg = load_config()
with DxmClient(cfg, login) as client:
    data = client.midou_balance()

print(json.dumps(data, ensure_ascii=False, indent=2))
