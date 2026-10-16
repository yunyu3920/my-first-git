"""登录身份识别 + 店铺核对。

用户 2026-10-16 明确：**店铺不固定、登录的店铺随时会改**。
所以：
  - 「有哪些登录身份」不能拿配置里那份静态店铺表当唯一来源；
  - 「店铺对不对」必须拿**表格里的店名**去对**登录账号里实际的店**。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from temu_bot.config import Config, ShopCfg


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


def write_session(cfg: Config, login: str, *, localstorage: bool = False) -> Path:
    state = cfg.data_dir / "state"
    state.mkdir(parents=True, exist_ok=True)
    path = state / f"session_{login}.json"
    path.write_text(json.dumps({"cookies": []}), encoding="utf-8")
    if localstorage:
        path.with_name(path.stem + "_localstorage.json").write_text("{}", encoding="utf-8")
    return path


# ------------------------------------------------------------ 有哪些登录身份
def test_logins_come_from_session_files(tmp_path):
    """有会话文件的才算"能用的身份"——不管配置里有没有登记。"""
    cfg = make_cfg(tmp_path)
    write_session(cfg, "some-account")

    logins = cfg.logins()
    assert [i.login for i in logins] == ["some-account"]
    assert logins[0].has_session and logins[0].ready
    assert not logins[0].known, "配置里没登记过，但照样识别出来"


def test_logins_do_not_need_the_config_shop_list(tmp_path):
    """**配置里一个店都没登记**，照样能识别出登录身份。"""
    cfg = make_cfg(tmp_path, shops=[])
    write_session(cfg, "a")
    write_session(cfg, "b")

    assert sorted(i.login for i in cfg.logins()) == ["a", "b"]


def test_config_shops_are_listed_even_before_login(tmp_path):
    """配置里登记过但还没登录 → 也要列出来（页面好显示"待登录"）。"""
    cfg = make_cfg(
        tmp_path, shops=[ShopCfg(name="某家店", login="an-account")]
    )
    logins = cfg.logins()

    assert [i.login for i in logins] == ["an-account"]
    assert logins[0].known is True
    assert logins[0].shop_hint == "某家店"
    assert logins[0].ready is False


def test_localstorage_sidecar_is_not_an_identity(tmp_path):
    """`session_x_localstorage.json` 是附属文件，不是另一个身份。"""
    cfg = make_cfg(tmp_path)
    write_session(cfg, "hat-tshirt", localstorage=True)

    logins = cfg.logins()
    assert [i.login for i in logins] == ["hat-tshirt"], f"多出了假身份：{[i.login for i in logins]}"


def test_profile_dirs_alone_do_not_create_identities(tmp_path):
    """只有浏览器缓存目录、没有会话文件 → **不算身份**。

    跑脚本留下的那种目录（探查/自检）就是这样，扫进来只会把下拉框塞满没用的东西。
    """
    junk = tmp_path / "profiles" / "some-probe"
    junk.mkdir(parents=True)
    (junk / "Cache").write_text("x", encoding="utf-8")

    cfg = make_cfg(tmp_path)
    assert cfg.logins() == []


def test_ready_logins_sort_first(tmp_path):
    cfg = make_cfg(tmp_path, shops=[ShopCfg(name="还没登的店", login="zzz")])
    write_session(cfg, "aaa")

    assert [i.login for i in cfg.logins()] == ["aaa", "zzz"]


# ------------------------------------------------------------ 拼装给页面看
def test_login_view_shape(tmp_path):
    from temu_bot.web.app import _login_view

    cfg = make_cfg(tmp_path)
    write_session(cfg, "hat-tshirt")
    view = _login_view(cfg.logins()[0])

    assert view["login"] == "hat-tshirt"
    assert view["ready"] is True
    assert view["has_session"] is True
    assert "profile_dir" in view and "session_file" in view


# ------------------------------------------------------------ 店铺核对（走接口）
@pytest.fixture()
def client(sandbox, monkeypatch):
    from fastapi.testclient import TestClient

    from temu_bot.web.app import AppState, create_app
    from temu_bot.web.jobs import JobRunner
    from temu_bot.web.logs import LogBuffer

    cfg = sandbox["cfg"]
    monkeypatch.setenv("TEMU_BOT_CONFIG", str(cfg.root / "config" / "config.yaml"))
    state = AppState(cfg=cfg, logs=LogBuffer(), runner=JobRunner())
    with TestClient(create_app(state=state), base_url="http://127.0.0.1") as c:
        c.temu_state = state
        yield c


class FakeDxm:
    """只实现核对店铺要用的两个方法。"""

    SHOPS = [
        {"id": 9013981, "name": "A hat tshirt"},
        {"id": 0, "name": "手工订单"},          # 占位桶，要滤掉
    ]

    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def shops(self):
        return list(self.SHOPS)

    def verify_shop(self, expected):
        from temu_bot.dianxiaomi.client import ShopCheck

        wanted = [expected] if isinstance(expected, str) else list(expected)
        names = [s["name"] for s in self.SHOPS]
        hit = next((n for n in names if n in wanted), "")
        return ShopCheck(
            expected=wanted, ok=bool(hit), matched_name=hit,
            matched_id="9013981" if hit else "", available=names,
        )


def test_verify_lists_the_accounts_real_shops(client, monkeypatch):
    """不传 expect → 只报"这个身份登录的账号里有哪些店"（占位桶要滤掉）。"""
    monkeypatch.setattr("temu_bot.dianxiaomi.client.DxmClient", FakeDxm)

    data = client.post("/api/shops/verify?login=hat-tshirt").json()
    assert data["ok"] is True
    assert [s["name"] for s in data["shops"]] == ["A hat tshirt"], "手工订单不该出现"
    assert "A hat tshirt" in data["message"]


def test_verify_passes_when_the_tables_shops_are_all_there(client, monkeypatch):
    monkeypatch.setattr("temu_bot.dianxiaomi.client.DxmClient", FakeDxm)

    data = client.post("/api/shops/verify?login=hat-tshirt&expect=A hat tshirt").json()
    assert data["ok"] is True
    assert data["missing"] == []


def test_verify_reports_the_missing_shop(client, monkeypatch):
    """表里用的店不在这个账号里 → 报出缺了哪家、账号里实际有哪些。"""
    monkeypatch.setattr("temu_bot.dianxiaomi.client.DxmClient", FakeDxm)

    data = client.post(
        "/api/shops/verify?login=hat-tshirt&expect=A lovely costume&A hat tshirt".replace(
            "&A hat tshirt", ",A hat tshirt"
        )
    ).json()

    assert data["ok"] is False
    assert data["missing"] == ["A lovely costume"]
    assert "A hat tshirt" in data["message"], "要把账号里实际有的店报出来"
    assert "没有" in data["message"]


def test_verify_tolerates_full_width_comma(client, monkeypatch):
    monkeypatch.setattr("temu_bot.dianxiaomi.client.DxmClient", FakeDxm)

    data = client.post(
        "/api/shops/verify", params={"login": "hat-tshirt", "expect": "A hat tshirt，不存在店"}
    ).json()
    assert data["missing"] == ["不存在店"]
