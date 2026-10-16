"""试出"按什么搜索"能过滤到目标商品。

表格里的「模板产品SPUID」= 8909735440，必须能在店小秘商品列表里定位到它，
否则"复制模板"就无从谈起。这里把 searchType / productSearchType 组合跑一遍，
看哪组真的过滤出了结果（totalSize 明显变小）。
"""

from __future__ import annotations

from temu_bot.config import load_config
from temu_bot.dianxiaomi.client import DxmClient

SPU = "8909735440"


def main() -> None:
    cfg = load_config()
    with DxmClient(cfg, "hat-tshirt") as client:
        print(f"目标 SPU = {SPU}\n")

        # 先看整体规模做基准
        base = client.list_products(dxm_state="online", page_size=1)
        print("在线产品总数（基准）:", (base.get("page") or {}).get("totalSize"))

        print("\n=== 试 searchType × productSearchType ===")
        for state in ("online", "offline"):
            for search_type in (0, 1, 2, 3, 4):
                for pst in (1, 2, 3):
                    try:
                        data = client.list_products(
                            dxm_state=state,
                            page_size=3,
                            search_type=search_type,
                            search_value=SPU,
                            product_search_type=pst,
                        )
                    except Exception as exc:  # noqa: BLE001
                        print(f"  [{state}] st={search_type} pst={pst} 异常 {str(exc)[:60]}")
                        continue
                    page = data.get("page") or {}
                    total = page.get("totalSize")
                    rows = page.get("list") or []
                    hit = [
                        f"{r.get('id')}/{r.get('productId')}/{str(r.get('productName'))[:16]}"
                        for r in rows[:2]
                    ]
                    mark = " ★" if isinstance(total, int) and 0 < total <= 10 else ""
                    print(f"  [{state}] st={search_type} pst={pst} → {total} 条 {hit}{mark}")


if __name__ == "__main__":
    main()
