"""**程序按表格里的店铺名自己认登录身份**（用户 2026-10-16 要求，界面不再手选）。

    「我们登录，程序核对确认即可。舍弃多余操作（登录身份的店铺输入框、核对店铺、
      打开店小秘按键）」

所以这里测：表格写「X」→ 程序挨个问身份 → 谁账号里有 X 就用谁；
都没有就停下并报清楚。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from temu_bot.config import Config, ShopCfg
from temu_bot.dianxiaomi.client import DxmError, ShopCheck, clear_lookup_cache, find_login_for_shop


def make_cfg(tmp_path: Path, shops: list[ShopCfg] | None = None) -> Config:
    cfg = Config(
        project={"table": tmp_path / "表.xlsx", "sheet_name": "Sheet1"},
        toolbox={"app_dir": tmp_path / "tb", "ledger": tmp_path / "tb" / "used.txt"},
    )
    cfg.root = tmp_path
    cfg.data_dir = tmp_path / "data"
    cfg.work_dir = tmp_path / "work"
    cfg.shops = shops or []
    cfg.ensure_dirs()
    return cfg


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_lookup_cache()
    yield
    clear_lookup_cache()


class FakeAccount:
    """一个假的登录身份：它的账号里有 `has_shops` 这些店。"""

    def __init__(self, cfg, login, has_shops):
        self.login = login
        self.has_shops = has_shops

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def shops(self):
        return [{"id": 100 + i, "name": n} for i, n in enumerate(self.has_shops)]

    def verify_shop(self, expected):
        wanted = [expected] if isinstance(expected, str) else list(expected)
        hit = next((n for n in self.has_shops if n in wanted), "")
        return ShopCheck(
            expected=wanted, ok=bool(hit), matched_name=hit,
            matched_id=str(100 + self.has_shops.index(hit)) if hit else "",
            available=list(self.has_shops),
        )


def make_factory(accounts: dict[str, list[str]]):
    def factory(login):
        if login not in accounts:
            raise AssertionError(f"不该去问身份「{login}」")
        return FakeAccount(None, login, accounts[login])

    return factory


def test_finds_the_account_that_has_the_shop(tmp_path):
    cfg = make_cfg(tmp_path)
    lookup = find_login_for_shop(
        cfg,
        "A hat tshirt",
        candidates=["lovely", "hat-tshirt"],
        client_factory=make_factory(
            {"lovely": ["A lovely costume"], "hat-tshirt": ["A hat tshirt"]}
        ),
    )

    assert lookup.ok
    assert lookup.login == "hat-tshirt"
    assert lookup.shop_id == "100"
    assert "A hat tshirt" in lookup.message()


def test_reports_when_no_account_has_the_shop(tmp_path):
    cfg = make_cfg(tmp_path)
    lookup = find_login_for_shop(
        cfg,
        "不存在的店",
        candidates=["lovely", "hat-tshirt"],
        client_factory=make_factory(
            {"lovely": ["A lovely costume"], "hat-tshirt": ["A hat tshirt"]}
        ),
    )

    assert not lookup.ok
    assert "没有任何登录身份" in lookup.message()
    assert lookup.available == ["A lovely costume", "A hat tshirt"], "要把见过的店报出来"
    assert {s["login"] for s in lookup.scanned} == {"lovely", "hat-tshirt"}
    assert all(s["reason"] == "账号里没有这家店" for s in lookup.scanned)


def test_a_broken_identity_does_not_kill_the_search(tmp_path):
    """一个身份问不动（没登录/接口挂）不该拖垮整轮——继续问下一个。"""
    cfg = make_cfg(tmp_path)

    def factory(login):
        if login == "broken":
            raise DxmError("登录态过期")
        return FakeAccount(None, login, ["A hat tshirt"])

    lookup = find_login_for_shop(
        cfg, "A hat tshirt", candidates=["broken", "good"], client_factory=factory
    )

    assert lookup.ok and lookup.login == "good"
    assert any(s["login"] == "broken" and "接口" in s["reason"] for s in lookup.scanned)


def test_result_is_cached_briefly(tmp_path):
    """一批几十行、店铺名往往一样，不必每行都去问一遍接口。"""
    cfg = make_cfg(tmp_path)
    calls: list[str] = []

    def factory(login):
        calls.append(login)
        return FakeAccount(None, login, ["A hat tshirt"])

    for _ in range(5):
        find_login_for_shop(cfg, "A hat tshirt", candidates=["a"], client_factory=factory)

    assert calls == ["a"], f"应该只问一次，实际问了 {len(calls)} 次"


def test_candidates_default_to_logins_with_a_session(tmp_path):
    """不给候选就按 `cfg.logins()` 来，**有会话文件的排前面**。"""
    cfg = make_cfg(tmp_path, shops=[ShopCfg(name="某店", login="not-logged-in")])
    state = cfg.data_dir / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "session_logged-in.json").write_text("{}", encoding="utf-8")

    asked: list[str] = []

    def factory(login):
        asked.append(login)
        return FakeAccount(None, login, ["某店"])

    lookup = find_login_for_shop(cfg, "某店", client_factory=factory)
    assert lookup.ok
    assert asked[0] == "logged-in", "先问登录过的身份，别一上来就问没登录的"


# ------------------------------------------------------------------ 走流水线
def test_pipeline_resolves_the_login_itself(cfg_with_shop, row, monkeypatch):
    """不给 login → 流水线自己认出来，并在 SHOP_CHECK 里说明是从哪认的。

    认身份那一步要走真接口（挨个问账号），所以这里把它换成假的——
    它自己的逻辑在上面几个用例里单独测过了。
    """
    from temu_bot.dianxiaomi.client import ShopLookup
    from tests.test_listing import DEFAULT_SHOP_ID, FakeClient, ListingPipeline
    from tests.test_listing import product_with_images

    asked: list[str] = []

    def fake_resolve(cfg, shop, **kwargs):
        asked.append(shop)
        return ShopLookup(shop=shop, login="hat-tshirt", shop_id=DEFAULT_SHOP_ID,
                          matched_name="A hat tshirt")

    monkeypatch.setattr("temu_bot.listing.find_login_for_shop", fake_resolve)

    client = FakeClient(
        template_row={"id": 111, "productName": "模板商品", "dxmState": "online"},
        product=product_with_images(),
        offline_rows=[{"id": 900, "productName": "别的商品"}],
        copy_adds={"id": 222, "productName": "模板商品"},
    )
    outcome = ListingPipeline(
        cfg_with_shop, row, login="", live=True, op=1, client_factory=lambda: client
    ).run()

    assert asked == ["A hat tshirt"], "应该拿表里的店铺名去认"
    assert not outcome.failed, [s.line() for s in outcome.steps]
    step = next(s for s in outcome.steps if s.name == "SHOP_CHECK")
    assert step.status.value == "ok"
    assert step.data["shop_id"] == DEFAULT_SHOP_ID
    assert "自动认出来的" in step.detail


def test_pipeline_stops_when_no_login_has_the_shop(cfg_with_shop, row, monkeypatch):
    """哪个账号里都没有这家店 → 停，并报出查过哪些身份。"""
    from tests.test_listing import FakeClient, ListingPipeline

    monkeypatch.setattr(
        "temu_bot.listing.find_login_for_shop",
        lambda cfg, shop, **kw: __import__(
            "temu_bot.dianxiaomi.client", fromlist=["ShopLookup"]
        ).ShopLookup(
            shop=shop,
            scanned=[{"login": "lovely", "reason": "账号里没有这家店"}],
            available=["A lovely costume"],
        ),
    )
    client = FakeClient()
    outcome = ListingPipeline(
        cfg_with_shop, row, login="", live=False, client_factory=lambda: client
    ).run()

    assert outcome.failed
    step = outcome.steps[-1] if outcome.steps[-1].name == "SHOP_CHECK" else next(
        s for s in outcome.steps if s.name == "SHOP_CHECK"
    )
    assert step.status.value == "fail"
    assert "没有任何登录身份" in step.detail
    assert not any("copy_product" in c for c in client.calls), "认不出就不能复制"
