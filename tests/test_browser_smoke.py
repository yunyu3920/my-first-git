"""浏览器层冒烟测试：真的启动一次 Chromium（无头），验证元素清单能抽出来。

浏览器不可用时 skip，不让整套测试因此变红。
"""

from __future__ import annotations

import pytest

from temu_bot.browser.base import BrowserError
from temu_bot.browser.inspect import collect, looks_logged_in
from temu_bot.browser.playwright_local import PlaywrightLocalProvider
from temu_bot.config import ShopCfg

HTML = """
<html><head><title>探测测试</title></head>
<body>
  <h1>商品编辑</h1>
  <input name="title" placeholder="请输入标题" />
  <button data-testid="save-btn">保存</button>
  <a href="/list">商品列表</a>
</body></html>
"""


@pytest.fixture()
def session(sandbox):
    cfg = sandbox["cfg"]
    cfg.browser.headless = True
    shop = ShopCfg(name="测试店铺", profile_dir=sandbox["tmp"] / "profiles" / "t")
    provider = PlaywrightLocalProvider()
    try:
        with provider.launch(cfg, shop) as s:
            yield s
    except BrowserError as exc:
        pytest.skip(f"本机浏览器不可用：{exc}")


def test_launch_and_collect_elements(session):
    page = session.page()
    page.set_content(HTML)
    data = collect(page)

    assert data["title"] == "探测测试"
    assert "商品编辑" in data["headings"]

    texts = {el.get("text") for el in data["elements"]}
    placeholders = {el.get("placeholder") for el in data["elements"]}
    testids = {el.get("testid") for el in data["elements"]}

    assert "保存" in texts
    assert "商品列表" in texts
    assert "请输入标题" in placeholders
    assert "save-btn" in testids


def test_profile_is_reused_across_sessions(sandbox):
    """同一个 profile 目录能连续开两次（登录态就是这么留住的）。"""
    cfg = sandbox["cfg"]
    cfg.browser.headless = True
    shop = ShopCfg(name="测试店铺", profile_dir=sandbox["tmp"] / "profiles" / "reuse")
    provider = PlaywrightLocalProvider()
    for _ in range(2):
        try:
            with provider.launch(cfg, shop) as s:
                s.page().set_content("<h1>ok</h1>")
                assert collect(s.page())["counts"]["a"] == 0
        except BrowserError as exc:
            pytest.skip(f"本机浏览器不可用：{exc}")


def test_looks_logged_in_url_heuristics(sandbox):
    cfg = sandbox["cfg"]

    class _FakePage:
        def locator(self, _sel):  # 没有配置候选元素时不会被调用
            raise AssertionError("不应被调用")

    assert looks_logged_in(cfg, "https://seller.example.com/login", _FakePage()) is False
    assert looks_logged_in(cfg, "https://seller.example.com/passport/sso", _FakePage()) is False
    assert looks_logged_in(cfg, "https://seller.example.com/product/list", _FakePage()) is True
    assert looks_logged_in(cfg, "about:blank", _FakePage()) is False
