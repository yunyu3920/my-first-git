"""登录窗口默认打开哪个网站：**必须是店小秘**，不能是 Temu 卖家后台。

踩过的坑：打包版没有带 `config/selectors/`，选择器配置读不到，
兜底值当时写的是 `seller.kuajingmaihuo.com`（Temu 卖家后台），
结果一登录就打开了 Temu。这里把兜底值钉死。
"""

from __future__ import annotations

from temu_bot.browser.inspect import DXM_HOME, login_start_url


def test_defaults_to_dianxiaomi_when_selectors_missing(sandbox):
    """配置/选择器都没了也要回退到店小秘。"""
    assert login_start_url(sandbox["cfg"]) == DXM_HOME
    assert "dianxiaomi.com" in DXM_HOME


def test_never_falls_back_to_temu(sandbox):
    assert "kuajingmaihuo.com" not in login_start_url(sandbox["cfg"])


def test_explicit_url_wins(sandbox):
    """命令行/接口显式给的地址优先。"""
    assert login_start_url(sandbox["cfg"], "https://example.com/x") == "https://example.com/x"


def test_uses_start_from_selectors_file(sandbox):
    cfg = sandbox["cfg"]
    path = cfg.root / "config" / "selectors" / "temu.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("urls:\n  start: https://www.dianxiaomi.com/custom\n", encoding="utf-8")
    assert login_start_url(cfg) == "https://www.dianxiaomi.com/custom"
