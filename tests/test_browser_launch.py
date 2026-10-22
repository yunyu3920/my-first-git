"""上架前"打开店小秘"这条链的测试。

用户要求：上架之前程序自动把网站打开，好让人①确认店铺信息②当场登录或切换店铺。
实现上是**另起一个进程**跑 `temu-bot login --shop X --until-closed`（不占服务线程），
所以这里测的是"命令拼对了、状态报对了、不叠窗"，**不真开浏览器**。
"""

from __future__ import annotations

import pytest

from temu_bot.web import browser_launch as bl


class FakeProc:
    def __init__(self, *, alive: bool = True) -> None:
        self.alive = alive
        self.terminated = False

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.terminated = True
        self.alive = False


@pytest.fixture(autouse=True)
def clean_registry():
    """每个用例前后都把"开着的窗口"清干净，免得互相串。"""
    bl._OPEN.clear()
    yield
    bl._OPEN.clear()


@pytest.fixture()
def spawned(monkeypatch):
    """把 subprocess.Popen 换掉，并记下拼出来的命令。"""
    calls: list[list[str]] = []

    def fake_popen(cmd, **kwargs):
        calls.append(list(cmd))
        assert kwargs.get("stdin") is not None, "别让子进程去读 stdin（没有终端）"
        return FakeProc()

    monkeypatch.setattr(bl.subprocess, "Popen", fake_popen)
    return calls


def test_open_builds_the_until_closed_command(spawned):
    r = bl.open_site("hat-tshirt")

    assert r["running"] is True
    cmd = spawned[0]
    assert "login" in cmd
    assert cmd[cmd.index("--shop") + 1] == "hat-tshirt"
    assert "--until-closed" in cmd, "子进程没有终端可按回车，必须用'关窗口为准'的模式"
    assert r["message"] and "关掉" in r["message"], "要告诉用户怎么算操作完了"


def test_open_passes_a_custom_url(spawned):
    bl.open_site("hat-tshirt", url="https://www.dianxiaomi.com/web/popTemu/list")
    cmd = spawned[0]
    assert cmd[cmd.index("--url") + 1] == "https://www.dianxiaomi.com/web/popTemu/list"


def test_second_open_is_refused_while_the_first_is_alive(spawned):
    """同一个身份别叠窗——浏览器 profile 本身也有锁，提前拦下能给人话提示。"""
    bl.open_site("hat-tshirt")

    with pytest.raises(bl.AlreadyOpen) as exc:
        bl.open_site("hat-tshirt")
    assert "已经开着" in str(exc.value)
    assert len(spawned) == 1, "第二次不该再起进程"


def test_after_the_window_closes_you_can_open_again(spawned, monkeypatch):
    bl.open_site("hat-tshirt")
    bl._OPEN["hat-tshirt"].process.alive = False        # 模拟用户关掉了窗口

    bl.open_site("hat-tshirt")
    assert len(spawned) == 2


def test_different_identities_can_be_open_together(spawned):
    bl.open_site("hat-tshirt")
    bl.open_site("lovely")
    assert len(spawned) == 2


def test_empty_login_is_rejected(spawned):
    with pytest.raises(ValueError):
        bl.open_site("   ")
    assert spawned == []


def test_status_reports_only_live_windows(spawned):
    bl.open_site("hat-tshirt")
    bl.open_site("lovely")
    bl._OPEN["lovely"].process.alive = False

    data = bl.status()
    assert list(data["open"]) == ["hat-tshirt"]
    assert data["any_running"] is True

    assert bl.status("lovely")["running"] is False
    assert bl.status("hat-tshirt")["running"] is True


def test_close_terminates_the_window(spawned):
    bl.open_site("hat-tshirt")
    r = bl.close("hat-tshirt")

    assert r["ok"] is True
    assert bl._OPEN["hat-tshirt"].process.terminated is True
    assert bl.status()["any_running"] is False


def test_close_when_nothing_open_is_honest():
    r = bl.close("hat-tshirt")
    assert r["ok"] is False
    assert "没开着" in r["message"]


def test_no_logins_falls_back_to_default_identity(monkeypatch):
    """全新用户一个登录态都没有：不报错，用 default 把登录窗口开起来。"""
    import temu_bot.dianxiaomi.client as client_mod

    class Cfg:
        def logins(self):
            return []

    class Miss:
        ok = False

    monkeypatch.setattr(client_mod, "find_login_for_shop", lambda cfg, shop: Miss())

    login, why = bl.pick_login_for_open(Cfg(), "A hat tshirt")
    assert login == "default"
    assert "还没有登录" in why


def test_wait_for_shop_login_returns_when_shop_appears(monkeypatch):
    import temu_bot.dianxiaomi.client as client_mod

    class Hit:
        ok = True

    monkeypatch.setattr(client_mod, "clear_lookup_cache", lambda: None)
    monkeypatch.setattr(client_mod, "find_login_for_shop", lambda cfg, shop: Hit())
    monkeypatch.setattr(bl, "LOGIN_POLL_SECONDS", 0)

    assert bl.wait_for_shop_login(object(), "A hat tshirt", "default", timeout_s=1) is True


def test_wait_for_shop_login_stops_when_window_closes(monkeypatch):
    """用户没登好就把窗口关了 → 不能傻等到 10 分钟。"""
    import temu_bot.dianxiaomi.client as client_mod

    class Miss:
        ok = False

    monkeypatch.setattr(client_mod, "clear_lookup_cache", lambda: None)
    monkeypatch.setattr(client_mod, "find_login_for_shop", lambda cfg, shop: Miss())
    monkeypatch.setattr(bl, "LOGIN_POLL_SECONDS", 0)

    proc = FakeProc(alive=False)
    assert (
        bl.wait_for_shop_login(
            object(), "A hat tshirt", "default", timeout_s=10, process=proc
        )
        is False
    )


def test_open_for_shop_waits_for_login(monkeypatch):
    monkeypatch.setattr(bl, "pick_login_for_open", lambda cfg, shop: ("default", "why"))
    monkeypatch.setattr(bl, "_session_exists", lambda cfg, login: False)
    monkeypatch.setattr(
        bl,
        "open_site",
        lambda login, **kw: {"login": login, "running": True, "message": "opened"},
    )
    monkeypatch.setattr(bl, "wait_for_shop_login", lambda *a, **kw: True)

    r = bl.open_for_shop(object(), "A hat tshirt", wait=True, wait_minutes=1)
    assert r["logged_in"] is True
    assert "登录成功" in r["message"]


def test_open_for_shop_cancels_and_closes_on_timeout(monkeypatch):
    """等满 10 分钟还没登录好 → 取消上架，并把登录窗口关掉。"""
    closed: list[str] = []
    monkeypatch.setattr(bl, "pick_login_for_open", lambda cfg, shop: ("default", "why"))
    monkeypatch.setattr(bl, "_session_exists", lambda cfg, login: False)
    monkeypatch.setattr(
        bl,
        "open_site",
        lambda login, **kw: {"login": login, "running": True, "message": "opened"},
    )
    monkeypatch.setattr(bl, "wait_for_shop_login", lambda *a, **kw: False)
    monkeypatch.setattr(bl, "close", lambda login: closed.append(login) or {"ok": True})

    r = bl.open_for_shop(object(), "A hat tshirt", wait=True, wait_minutes=10)
    assert r["logged_in"] is False
    assert "已取消上架" in r["message"]
    assert closed == ["default"], "超时后要把登录窗口关掉"


def test_open_for_shop_wait_tolerates_an_already_open_window(monkeypatch):
    """上一次的登录窗口还开着 → 不报 409，继续等它登录完成。"""
    monkeypatch.setattr(bl, "pick_login_for_open", lambda cfg, shop: ("default", "why"))
    monkeypatch.setattr(bl, "_session_exists", lambda cfg, login: False)

    def boom(login, **kw):
        raise bl.AlreadyOpen("已经开着")

    monkeypatch.setattr(bl, "open_site", boom)
    monkeypatch.setattr(bl, "wait_for_shop_login", lambda *a, **kw: True)

    r = bl.open_for_shop(object(), "A hat tshirt", wait=True, wait_minutes=1)
    assert r["logged_in"] is True


def test_cli_command_prefers_the_installed_entry(monkeypatch, tmp_path):
    """源码跑的时候优先用 .venv\\Scripts\\temu-bot.exe；没有就退回 python -m。"""
    import sys

    fake_exe = tmp_path / "temu-bot.exe"
    fake_exe.write_bytes(b"x")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))

    assert bl._cli_command() == [str(fake_exe)]

    fake_exe.unlink()
    cmd = bl._cli_command()
    assert cmd[1:] == ["-m", "temu_bot.cli"], cmd
