"""上架前先核对店铺：表里要上的店，在**实时登录的账号**里找不找得到。

用户 2026-10-17 要求：

    先核对店小秘，无误再开始上架；有误就提醒，**不要执行上架**。
    把「核对提醒」和「上架执行」分开来。

以前是每一行都跑到 `SHOP_CHECK` 才失败，98 行就刷 98 条一模一样的错误。
现在把核对提前成一次**只读的批量动作**：先把这批行用到的店铺名去重，
逐个拿 `userIn.shopMap` 实时对一遍；只要有一个对不上，整批都不开跑。

判定口径跟流水线里完全一致：**只看表里第 10 列的店铺名 ↔ 实时读到的账号店铺**，
`config.shops` 一律不参与判定（它只是提示，随时会过期）。
"""

from __future__ import annotations

from typing import Any

from temu_bot.config import Config
from temu_bot.dianxiaomi.client import find_login_for_shop
from temu_bot.sheet.table import SheetTable


def _row_range(rows: list[int]) -> str:
    """[2,3,4,9] → "2–4、9"（连续行号收成区间，报错话术短一点）。"""
    nums = sorted(set(rows))
    if not nums:
        return "-"
    parts: list[str] = []
    start = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        parts.append(f"{start}–{prev}" if prev > start else str(start))
        start = prev = n
    parts.append(f"{start}–{prev}" if prev > start else str(start))
    return "、".join(parts)


def precheck_shops(cfg: Config, row_indexes: list[int]) -> dict[str, Any]:
    """核对这批行用到的店铺。**只读，不碰任何写操作。**

    返回::

        {
          "ok": bool,
          "message": str,          # 给人看的一句话（失败时是多行，逐店说明）
          "checks": [              # 每个店一条
            {"shop", "rows", "ok", "login", "shop_id", "matched_name", "message"}
          ],
        }
    """
    wanted = set(row_indexes)
    shop_rows: dict[str, list[int]] = {}

    for row in SheetTable.from_config(cfg).iter_rows():
        if row.row_index in wanted:
            shop_rows.setdefault((row.shop or "").strip(), []).append(row.row_index)

    if not shop_rows:
        return {
            "ok": False,
            "message": "选中的行在表里一个都找不到（可能是空白行，已按规则跳过）",
            "checks": [],
        }

    # 每次核对都实时问接口：用户可能刚在浏览器里登好、或切了店。
    # （find_login_for_shop 本身已经不做缓存，这里不再需要清缓存。）
    checks: list[dict[str, Any]] = []
    for shop, rows in shop_rows.items():
        if not shop:
            checks.append(
                {
                    "shop": "",
                    "rows": rows,
                    "ok": False,
                    "login": "",
                    "shop_id": "",
                    "matched_name": "",
                    "message": f"第 {_row_range(rows)} 行没填「店铺名」（第 10 列）",
                }
            )
            continue

        lookup = find_login_for_shop(cfg, shop)
        checks.append(
            {
                "shop": shop,
                "rows": rows,
                "ok": lookup.ok,
                "login": lookup.login,
                "shop_id": lookup.shop_id,
                "matched_name": lookup.matched_name,
                "message": lookup.message(),
            }
        )

    ok = all(c["ok"] for c in checks)
    if ok:
        message = "✅ 店铺核对通过：" + "；".join(
            f"「{c['shop']}」→ 登录身份「{c['login']}」(shopId={c['shop_id']})"
            for c in checks
        )
    else:
        lines = [
            f"· 第 {_row_range(c['rows'])} 行：{c['message']}"
            for c in checks
            if not c["ok"]
        ]
        message = "⛔ 店铺核对没通过，还没有开始上架：\n" + "\n".join(lines)

    return {"ok": ok, "message": message, "checks": checks}
