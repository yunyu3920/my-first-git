"""上架前的店铺核对：一次查清，别让每一行各刷一条同样的错。"""

from __future__ import annotations

from temu_bot.dianxiaomi.client import ShopLookup
from temu_bot.web import shopcheck


def test_no_login_state_is_reported_not_crashed(sandbox):
    """沙箱里没有任何登录态 → 明确说"没找到"，而不是抛异常。"""
    result = shopcheck.precheck_shops(sandbox["cfg"], [2])
    assert result["ok"] is False
    assert result["checks"][0]["shop"] == "A lovely costume"
    assert "没有开始上架" in result["message"]


def test_same_shop_is_checked_once_for_all_rows(sandbox, monkeypatch):
    """第 2、3 行是同一家店 → 只查一次，行号合在一起报。"""

    def fake_lookup(cfg, shop, **kw):
        return ShopLookup(
            shop=shop, login="hat-tshirt", shop_id="8665618", matched_name=shop
        )

    monkeypatch.setattr(shopcheck, "find_login_for_shop", fake_lookup)
    result = shopcheck.precheck_shops(sandbox["cfg"], [2, 3])
    assert result["ok"] is True
    assert len(result["checks"]) == 1
    assert result["checks"][0]["rows"] == [2, 3]
    assert "8665618" in result["message"]


def test_empty_shop_name_fails_with_row_numbers(sandbox, tmp_path):
    from tests.conftest import make_table

    cfg = sandbox["cfg"]
    cfg.project.table = make_table(
        tmp_path / "没店铺.xlsx",
        [{1: "C:/tmp/a", 2: "a.jpg", 9: "1", 11: "1", 17: "标题"}],
    )
    result = shopcheck.precheck_shops(cfg, [2])
    assert result["ok"] is False
    assert "没填「店铺名」" in result["checks"][0]["message"]


def test_one_bad_shop_blocks_the_whole_batch(sandbox, monkeypatch):
    """两家店、一家对不上 → 整批不开跑。"""

    def fake_lookup(cfg, shop, **kw):
        if shop == "A lovely costume":
            return ShopLookup(
                shop=shop, login="hat-tshirt", shop_id="1", matched_name=shop
            )
        return ShopLookup(shop=shop, scanned=[{"login": "hat-tshirt", "reason": "账号里没有这家店"}])

    from tests.conftest import make_table

    cfg = sandbox["cfg"]
    cfg.project.table = make_table(
        cfg.root / "两家店.xlsx",
        [
            {1: "C:/tmp/a", 2: "a.jpg", 9: "1", 10: "A lovely costume", 11: "1", 17: "标题"},
            {1: "C:/tmp/a", 2: "b.jpg", 9: "2", 10: "SWEET SHIRT SHOP", 11: "1", 17: "标题2"},
        ],
    )
    monkeypatch.setattr(shopcheck, "find_login_for_shop", fake_lookup)

    result = shopcheck.precheck_shops(cfg, [2, 3])
    assert result["ok"] is False
    assert len(result["checks"]) == 2
    bad = [c for c in result["checks"] if not c["ok"]]
    assert bad and bad[0]["shop"] == "SWEET SHIRT SHOP"
