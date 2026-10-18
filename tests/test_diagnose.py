"""接口自检：正常 / 结构变了 / 404 / 没有登录态。"""

from __future__ import annotations

import json

import httpx

from temu_bot.diagnose import ApiCheck, ApiReport, check_api, write_report
from temu_bot.dianxiaomi.client import DxmClient
from temu_bot.dianxiaomi.session import ShopSession, session_path

LOGIN = "hat-tshirt"


def make_session(cfg, login: str = LOGIN) -> None:
    ShopSession(
        shop=login,
        cookies=[
            {
                "name": "JSESSIONID",
                "value": "x",
                "domain": ".dianxiaomi.com",
                "path": "/",
            }
        ],
    ).save(session_path(cfg, login))


def good_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/api/userIn.json":
        body = {"code": 0, "data": {"shopMap": {"popTemu": [{"id": 1, "name": "A hat tshirt"}]}}}
    elif path == "/api/popTemuProduct/pageList.json":
        body = {"code": 0, "data": {"page": {"list": [{"id": 123}], "totalSize": 1}}}
    elif path == "/api/album/index.json":
        body = {
            "code": 0,
            "data": {"albumUserSummary": {"sizeAllow": 100, "sizeUsed": 10, "picNum": 1}},
        }
    elif path == "/api/popTemuProduct/edit.json":
        body = {"code": 0, "data": {"product": {"id": 123, "productName": "x"}}}
    else:
        return httpx.Response(404, request=request)
    return httpx.Response(200, json=body, request=request)


def client_factory(cfg, login):
    return _Client(cfg, login, good_handler)


class _Client:
    """把 transport 注进去，避免真网络。"""

    def __init__(self, cfg, login, handler):
        self._inner = DxmClient(cfg, login, transport=httpx.MockTransport(handler))

    def __enter__(self):
        return self._inner.__enter__()

    def __exit__(self, *exc):
        return self._inner.__exit__(*exc)


def test_all_checks_pass_with_deep(sandbox):
    cfg = sandbox["cfg"]
    make_session(cfg)

    report = check_api(cfg, LOGIN, deep=True, client_factory=client_factory)

    assert report.ok is True
    assert [c.name for c in report.checks] == [
        "登录态 userIn",
        "商品列表 pageList",
        "图片空间 album",
        "商品详情 edit",
    ]


def test_missing_album_summary_is_reported(sandbox):
    cfg = sandbox["cfg"]
    make_session(cfg)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/album/index.json":
            return httpx.Response(200, json={"code": 0, "data": {}}, request=request)
        return good_handler(request)

    report = check_api(cfg, LOGIN, client_factory=lambda c, l: _Client(c, l, handler))
    album = next(c for c in report.checks if c.name.startswith("图片空间"))
    assert album.ok is False
    assert "albumUserSummary" in album.detail
    assert report.ok is False


def test_404_counts_as_site_changed(sandbox):
    cfg = sandbox["cfg"]
    make_session(cfg)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/popTemuProduct/pageList.json":
            return httpx.Response(404, text="not found", request=request)
        return good_handler(request)

    report = check_api(cfg, LOGIN, client_factory=lambda c, l: _Client(c, l, handler))
    page = next(c for c in report.checks if c.name.startswith("商品列表"))
    assert page.ok is False
    assert "疑似接口变更" in page.detail


def test_expired_session_is_reported_as_login_failure(sandbox):
    """code=2001 是登录失效，不能误报成"接口结构变了"。"""
    cfg = sandbox["cfg"]
    make_session(cfg)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"code": 2001, "msg": "验证失败了", "data": None}, request=request
        )

    report = check_api(cfg, LOGIN, client_factory=lambda c, l: _Client(c, l, handler))

    assert report.ok is False
    assert "登录态" in report.error
    assert report.checks[0].ok is False
    assert "验证失败" in report.checks[0].detail


def test_no_session_is_reported_not_raised(sandbox):
    report = check_api(sandbox["cfg"], "nobody")
    assert report.ok is False
    assert "登录态" in report.error


def test_write_report_creates_json(sandbox):
    cfg = sandbox["cfg"]
    report = ApiReport(login=LOGIN, checks=[ApiCheck("登录态 userIn", True, "ok")])
    path = write_report(cfg, report)

    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["login"] == LOGIN
    assert data["checks"][0]["name"] == "登录态 userIn"
