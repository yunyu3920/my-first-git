"""店小秘接口客户端测试：全部走 httpx.MockTransport，不发真实网络请求。"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest

from temu_bot.browser.recorder import Recorder
from temu_bot.dianxiaomi.client import DxmClient, DxmError, NotLoggedIn, ShopMismatch
from temu_bot.dianxiaomi.session import ShopSession, load_session, save_session_from_context, session_path

SHOP = "A lovely costume"


def make_session(cfg, *, expires: float | None = None) -> ShopSession:
    session = ShopSession(
        shop=SHOP,
        cookies=[
            {
                "name": "dxm_session",
                "value": "test-value",
                "domain": ".dianxiaomi.com",
                "path": "/",
                "expires": expires if expires is not None else 2000000000,
            }
        ],
        headers={"user-agent": "pytest-agent"},
        exported_at="2026-10-14T00:00:00",
    )
    session.save(session_path(cfg, SHOP))
    return session


def json_transport(seen: dict | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen["url"] = str(request.url)
            seen["headers"] = dict(request.headers)
        return httpx.Response(200, json={"success": True, "data": {"id": "1"}}, request=request)

    return httpx.MockTransport(handler)


def html_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="<!DOCTYPE html><html><body>请登录</body></html>",
            headers={"content-type": "text/html"},
            request=request,
        )

    return httpx.MockTransport(handler)


def test_requires_session(sandbox):
    cfg = sandbox["cfg"]
    with pytest.raises(NotLoggedIn) as exc:
        DxmClient(cfg, SHOP)
    assert "login" in str(exc.value)


def test_expired_session_is_rejected(sandbox):
    cfg = sandbox["cfg"]
    make_session(cfg, expires=1_000_000)  # 2001 年就过期了
    session = load_session(cfg, SHOP)
    assert session is not None and session.looks_expired()
    with pytest.raises(NotLoggedIn) as exc:
        DxmClient(cfg, SHOP)
    assert "过期" in str(exc.value)


def test_health_check_ok_and_cookie_is_sent(sandbox):
    cfg = sandbox["cfg"]
    make_session(cfg)
    seen: dict = {}
    with DxmClient(cfg, SHOP, transport=json_transport(seen)) as client:
        result = client.health_check()

    assert result["ok"] is True
    assert result["data_keys"] == ["data", "success"]
    assert seen["url"].endswith("/api/userIn.json")
    assert "dxm_session=test-value" in seen["headers"].get("cookie", "")
    assert seen["headers"].get("user-agent") == "pytest-agent"


def test_html_response_means_logged_out(sandbox):
    cfg = sandbox["cfg"]
    make_session(cfg)
    with DxmClient(cfg, SHOP, transport=html_transport()) as client:
        result = client.health_check()
    assert result["ok"] is False
    assert result["reason"] == "登录态失效"


def test_http_error_raises_dxm_error(sandbox):
    cfg = sandbox["cfg"]
    make_session(cfg)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom", request=request)

    with DxmClient(cfg, SHOP, transport=httpx.MockTransport(handler)) as client:
        result = client.health_check()
    assert result["ok"] is False
    assert result["reason"] == "接口异常"


def test_non_json_response_raises(sandbox):
    cfg = sandbox["cfg"]
    make_session(cfg)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json", headers={"content-type": "text/plain"}, request=request)

    with DxmClient(cfg, SHOP, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DxmError):
            client.product_edit("184807703165778961")


def test_health_check_detects_success_false(sandbox):
    """JSON 但 success=false —— 也是未登录，不能算可用。"""
    cfg = sandbox["cfg"]
    make_session(cfg)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"success": False, "msg": "请先登录", "code": 0}, request=request
        )

    with DxmClient(cfg, SHOP, transport=httpx.MockTransport(handler)) as client:
        result = client.health_check()

    assert result["ok"] is False
    assert result["reason"] == "接口返回未登录"
    assert "请先登录" in result["detail"]
    assert "success" in result["sample"]


def test_health_check_reports_sample_shape(sandbox):
    cfg = sandbox["cfg"]
    make_session(cfg)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "success": True,
                "shopName": "A lovely costume",
                "data": {"userName": "abc", "big": {"nested": 1}},
            },
            request=request,
        )

    with DxmClient(cfg, SHOP, transport=httpx.MockTransport(handler)) as client:
        result = client.health_check()

    assert result["ok"] is True
    assert "shopName='A lovely costume'" in result["sample"]   # 简单值直接给
    assert "userName" in result["sample"]                      # 嵌套对象的键名要带出来
    assert "big" in result["sample"]


def test_health_check_real_logged_out_shape(sandbox):
    """实测过的未登录返回：{"code":2001,"msg":"验证失败了","msgCode":null,"data":null}"""
    cfg = sandbox["cfg"]
    make_session(cfg)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"code": 2001, "msg": "验证失败了", "msgCode": None, "data": None}, request=request
        )

    with DxmClient(cfg, SHOP, transport=httpx.MockTransport(handler)) as client:
        result = client.health_check()

    assert result["ok"] is False
    assert result["reason"] == "接口返回未登录"
    assert "2001" in result["detail"] or "验证失败" in result["detail"]


def test_verdict_of_rules():
    from temu_bot.dianxiaomi.client import verdict_of

    assert verdict_of({"code": 2001, "msg": "验证失败了", "data": None})[0] is False
    assert verdict_of({"success": False, "msg": "请先登录"})[0] is False
    assert verdict_of({"success": True, "data": {"a": 1}})[0] is True
    assert verdict_of({"code": 200, "data": [1, 2]})[0] is True
    assert verdict_of({"whatever": 1})[0] is None        # 看不出来 → 交给调用方
    assert verdict_of("not a dict")[0] is None


def test_known_endpoints_hit_expected_paths(sandbox):
    cfg = sandbox["cfg"]
    make_session(cfg)
    calls: list[tuple[str, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read().decode("utf-8", "replace") if request.method == "POST" else ""
        calls.append((request.method, request.url.path, body))
        return httpx.Response(200, json={"success": True, "data": {"page": {"list": []}}}, request=request)

    with DxmClient(cfg, SHOP, transport=httpx.MockTransport(handler)) as client:
        client.user_info()
        client.product_edit("123")
        client.album_index()
        client.list_products(dxm_state="offline")
        client.copy_product("8909735440", "9013981")
        client.batch_publish(["1", "2"])

    paths = {(m, p) for m, p, _ in calls}
    assert ("GET", "/api/userInfo.json") in paths
    assert ("GET", "/api/popTemuProduct/edit.json") in paths
    assert ("GET", "/api/album/index.json") in paths
    assert ("POST", "/api/popTemuProduct/pageList.json") in paths
    assert ("POST", "/api/banjia/copyProduct.json") in paths
    assert ("POST", "/api/product/popTemu/batchPublish.json") in paths


def test_album_delete_matches_by_cos_path_not_name(sandbox):
    """删图片空间里的图：只按 COS 路径精确命中，同名老图不碰。"""
    cfg = sandbox["cfg"]
    make_session(cfg)
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/album/list.json":
            name = request.url.params.get("name", "")
            rows = [
                {"idStr": "111", "name": name, "imgUrl": "/wxalbum/1/20261018/a.jpg", "lockState": 0},
                {"idStr": "222", "name": name, "imgUrl": "/wxalbum/1/20261017/old.jpg", "lockState": 0},
            ]
            return httpx.Response(
                200,
                json={"code": 0, "data": {"picListPage": {"list": rows}}},
                request=request,
            )
        if path == "/api/album/delPic.json":
            seen["body"] = request.read().decode()
            return httpx.Response(200, json={"code": 0, "msg": "ok"}, request=request)
        return httpx.Response(404, request=request)

    with DxmClient(cfg, SHOP, transport=httpx.MockTransport(handler)) as client:
        found = client.album_find_by_urls(
            ["https://wxalbum-10001658-cos.dianxiaomi.com/wxalbum/1/20261018/a.jpg"]
        )
        assert [i["idStr"] for i in found] == ["111"], "同名的老图不能命中"
        client.album_delete(["111"])

    assert "ids=111" in seen["body"]


def test_copy_product_payload_matches_frontend(sandbox):
    """复制商品的 form 参数必须与前端「选择店铺」弹窗一致。"""
    cfg = sandbox["cfg"]
    make_session(cfg)
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "copyProduct" in request.url.path:
            seen["body"] = request.read().decode()
        return httpx.Response(200, json={"success": True}, request=request)

    with DxmClient(cfg, SHOP, transport=httpx.MockTransport(handler)) as client:
        client.copy_product("220114595100702322", "9013981")

    body = seen["body"]
    assert "shopIds=9013981" in body
    assert "productId=220114595100702322" in body
    assert "platform=popTemu" in body
    assert "hidePrice=0" in body


def test_batch_publish_uses_ids_param(sandbox):
    cfg = sandbox["cfg"]
    make_session(cfg)
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "batchPublish" in request.url.path:
            seen["body"] = request.read().decode()
        return httpx.Response(200, json={"success": True}, request=request)

    with DxmClient(cfg, SHOP, transport=httpx.MockTransport(handler)) as client:
        client.batch_publish(["11", "22"])

    assert seen["body"] == "ids=11%2C22" or seen["body"] == "ids=11,22"


def test_recorder_redacts_secrets():
    safe, cookie_names = Recorder._clean_headers(
        {
            "Cookie": "a=1; b=2",
            "Authorization": "Bearer secret",
            "X-CSRF-TOKEN": "tok",
            "User-Agent": "UA",
            "Referer": "https://www.dianxiaomi.com/",
        }
    )
    assert cookie_names == ["a", "b"]
    assert "Cookie" not in safe
    assert safe["Authorization"] == "<已隐藏>"
    assert safe["X-CSRF-TOKEN"] == "<已隐藏>"
    assert safe["User-Agent"] == "UA"


def test_recorder_writes_jsonl(tmp_path, sandbox):
    cfg = sandbox["cfg"]
    recorder = Recorder(cfg, tmp_path / "rec")
    recorder.close()

    # 直接喂一个假的 request 对象，验证筛选与脱敏逻辑
    class FakeRequest:
        url = "https://www.dianxiaomi.com/api/userIn.json"
        method = "GET"
        resource_type = "xhr"
        post_data = None
        headers = {"Cookie": "s=1", "Accept": "application/json"}

    recorder._handle = (tmp_path / "rec" / "x.jsonl").open("w", encoding="utf-8")
    recorder._on_request(FakeRequest())
    recorder.close()

    lines = (tmp_path / "rec" / "x.jsonl").read_text(encoding="utf-8").strip().splitlines()
    payload = json.loads(lines[0])
    assert payload["kind"] == "request"
    assert payload["method"] == "GET"
    assert payload["cookie_names"] == ["s"]
    assert "Cookie" not in payload["headers"]

    # 静态资源不记
    class FakeStatic(FakeRequest):
        url = "https://www.dianxiaomi.com/static/app.js"
        resource_type = "script"

    recorder2 = Recorder(cfg, tmp_path / "rec2")
    recorder2._on_request(FakeStatic())
    assert recorder2.request_count == 0
    recorder2.close()

    # 但整页跳转（document）要记
    class FakeNav(FakeRequest):
        url = "https://www.dianxiaomi.com/web/popTemu/edit?id=1"
        resource_type = "document"

    recorder3 = Recorder(cfg, tmp_path / "rec3")
    recorder3._on_request(FakeNav())
    assert recorder3.request_count == 1
    recorder3.close()


def shop_transport(shops: list[dict]) -> httpx.MockTransport:
    """模拟 /api/userIn.json 的 shopMap 返回。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": 0,
                "msg": "Successful",
                "data": {"shopMap": {"pddkj": shops}},
            },
            request=request,
        )

    return httpx.MockTransport(handler)


def test_verify_shop_ok(sandbox):
    cfg = sandbox["cfg"]
    make_session(cfg)
    transport = shop_transport([{"idStr": "9013981", "name": "A hat tshirt", "platform": "pddkj"}])

    with DxmClient(cfg, SHOP, transport=transport) as client:
        result = client.verify_shop("A hat tshirt")

    assert result.ok is True
    assert result.matched_id == "9013981"
    assert "店铺一致" in result.message()


def test_verify_shop_mismatch_lists_available(sandbox):
    """这是用户明确要求的安全闸：表里要上的店不在当前登录里 → 必须拦住并说清有哪些店。"""
    cfg = sandbox["cfg"]
    make_session(cfg)
    transport = shop_transport([{"idStr": "9013981", "name": "A hat tshirt", "platform": "pddkj"}])

    with DxmClient(cfg, SHOP, transport=transport) as client:
        result = client.verify_shop("A lovely costume")
        assert result.ok is False
        message = result.message()
        assert "A lovely costume" in message
        assert "A hat tshirt" in message           # 要把可选的店列出来
        assert "切换" in message

        with pytest.raises(ShopMismatch):
            client.assert_shop("A lovely costume")


def test_verify_shop_accepts_multiple_expected_names(sandbox):
    cfg = sandbox["cfg"]
    make_session(cfg)
    transport = shop_transport(
        [
            {"idStr": "9013981", "name": "A hat tshirt"},
            {"idStr": "9022222", "name": "A lovely costume"},
        ]
    )
    with DxmClient(cfg, SHOP, transport=transport) as client:
        assert client.verify_shop(["A lovely costume"]).ok is True
        assert client.verify_shop(["不存在的店", "A hat tshirt"]).ok is True
        assert client.verify_shop(["不存在的店"]).ok is False


def test_recorder_captures_document_navigation(tmp_path, sandbox):
    """整页跳转也要录——否则"点一下跳到编辑页"看起来像没反应。"""
    from temu_bot.browser.recorder import _is_interesting

    assert _is_interesting("https://www.dianxiaomi.com/api/userIn.json", "xhr")
    assert _is_interesting("https://wxalbum-1-cos.dianxiaomi.com/wxalbum/x.jpg", "fetch")
    assert _is_interesting("https://www.dianxiaomi.com/web/popTemu/edit?id=123", "document")
    # 静态资源仍然不录
    assert not _is_interesting("https://www.dianxiaomi.com/static/app.js", "script")
    assert not _is_interesting("https://www.dianxiaomi.com/img/a.png", "image")
    assert _is_interesting("https://www.dianxiaomi.com/web/popTemu/edit", "xhr")
    assert not _is_interesting("https://qiyukf.com/script/x.js", "script")


def test_recorder_captures_dialogs_console_and_failures(tmp_path, sandbox):
    """弹窗/JS 报错/失败请求都要被记下来——"点了没反应"的三大元凶。"""
    cfg = sandbox["cfg"]
    recorder = Recorder(cfg, tmp_path / "rec")

    class FakeDialog:
        type = "confirm"
        message = "确认要编辑该商品吗？"

        def __init__(self):
            self.accepted = False

        def accept(self):
            self.accepted = True

    class FakeConsole:
        type = "error"
        text = "Uncaught TypeError: x is undefined"
        location = "app.js:1:2"

    class FakeRequest:
        method = "POST"
        url = "https://www.dianxiaomi.com/api/popTemuProduct/edit.json"
        failure = "net::ERR_ABORTED"

    dialog = FakeDialog()
    recorder._on_dialog(dialog)
    recorder._on_console(FakeConsole())
    recorder._on_pageerror(RuntimeError("boom"))
    recorder._on_request_failed(FakeRequest())

    assert dialog.accepted is True                     # 弹窗必须被点掉，否则流程卡死
    summary = recorder.summary()
    assert summary["dialogs"] == ["[confirm] 确认要编辑该商品吗？"]
    assert any("TypeError" in item for item in summary["console_errors"])
    assert any("boom" in item for item in summary["console_errors"])
    assert summary["failed_requests"] and "ERR_ABORTED" in summary["failed_requests"][0]

    recorder.close()
    kinds = {
        json.loads(line)["kind"]
        for line in recorder.jsonl_path.read_text(encoding="utf-8").strip().splitlines()
    }
    assert {"dialog", "console", "pageerror", "requestfailed"} <= kinds


def test_recorder_writes_large_request_body_to_file(tmp_path, sandbox):
    """保存商品那种大 body（商品对象塞在 multipart 里）必须完整落盘，不能被截断。"""
    cfg = sandbox["cfg"]
    recorder = Recorder(cfg, tmp_path / "rec")

    big_payload = '{"productName": "' + "X" * 60_000 + '"}'

    class FakeBigRequest:
        url = "https://www.dianxiaomi.com/api/popTemuProduct/add3f8c2a97.json"
        method = "POST"
        resource_type = "xhr"
        post_data = big_payload
        headers = {"Content-Type": "multipart/form-data; boundary=----x"}

    recorder._on_request(FakeBigRequest())
    recorder.close()

    lines = [json.loads(x) for x in recorder.jsonl_path.read_text(encoding="utf-8").strip().splitlines()]
    entry = lines[0]
    assert entry["body_len"] == len(big_payload)
    assert entry["post_data"] is None          # 不内联，避免把日志撑爆
    body_file = Path(entry["body_file"])
    assert body_file.exists()
    assert body_file.read_text(encoding="utf-8") == big_payload   # 一个字节都不能少


def test_recorder_falls_back_to_post_data_buffer(tmp_path, sandbox):
    """multipart 请求的 post_data 是 None，必须退到 post_data_buffer 才拿得到内容。"""
    cfg = sandbox["cfg"]
    recorder = Recorder(cfg, tmp_path / "recbuf")

    # 真实保存请求的 body 是 30KB+，这里按量级构造（超过落盘阈值）
    filler = "A" * 6000
    multipart = (
        "------WebKitFormBoundaryX\r\n"
        'Content-Disposition: form-data; name="file"; filename="choiceSave.txt"\r\n'
        "Content-Type: text/plain\r\n\r\n"
        '{"productName":"T-Shirt","description":"' + filler + '"}\r\n'
        "------WebKitFormBoundaryX\r\n"
        'Content-Disposition: form-data; name="op"\r\n\r\n1\r\n'
        "------WebKitFormBoundaryX--\r\n"
    )

    class FakeMultipart:
        url = "https://www.dianxiaomi.com/api/popTemuProduct/add3f8c2a97.json"
        method = "POST"
        resource_type = "xhr"
        post_data = None                      # ← 关键：multipart 时是 None
        post_data_buffer = multipart.encode()
        headers = {"content-type": "multipart/form-data; boundary=----WebKitFormBoundaryX"}

    recorder._on_request(FakeMultipart())
    recorder.close()

    entry = json.loads(recorder.jsonl_path.read_text(encoding="utf-8").strip().splitlines()[0])
    assert entry["body_len"] == len(multipart)
    body_file = Path(entry["body_file"])
    assert body_file.exists()
    text = body_file.read_text(encoding="utf-8")
    assert "choiceSave.txt" in text
    assert '{"productName":"T-Shirt"' in text     # 商品对象 JSON 在 file 部分里
    assert 'name="op"' in text                    # op 作为另一个表单字段
    assert filler in text                         # 长内容一个字节都没丢


def test_recorder_keeps_small_request_body_inline(tmp_path, sandbox):
    cfg = sandbox["cfg"]
    recorder = Recorder(cfg, tmp_path / "rec2")

    class FakeSmall:
        url = "https://www.dianxiaomi.com/api/cos/getSign.json"
        method = "POST"
        resource_type = "xhr"
        post_data = "bucket=wxalbum&region=ap-shanghai&fileName=a.jpg"
        headers = {}

    recorder._on_request(FakeSmall())
    recorder.close()
    entry = json.loads(recorder.jsonl_path.read_text(encoding="utf-8").strip().splitlines()[0])
    assert entry["body_file"] is None
    assert entry["post_data"].startswith("bucket=wxalbum")


# 抓包实测的保存载荷字段（2026-10-15，真实 choiceSave.txt 的 39 个键）
RECORDED_SAVE_KEYS = {
    "attributes", "categoryId", "categoryType", "description", "dxmDetailVideoId", "dxmPdfUrl",
    "dxmState", "dxmVideoId", "freightTemplateId", "fullCid", "goodsModel", "id",
    "instructionsId", "instructionsName", "instructionsTranslateId", "mainProductSkuSpecReqs",
    "materialImgUrl", "op", "optionValue", "originFileUrl", "outerGoodsUrl",
    "personalizationSwitch", "productId", "productName", "productNameI18n", "productOrigin",
    "productSaleExtAttrReq", "productSemiManagedReq", "productWarehouseRouteReq", "qualifiedEn",
    "region1ShortNames", "region2Id", "sensitiveAttr", "shipmentLimitSecond", "shopId",
    "sizeCharts", "sizeTemplateIds", "sourceUrl", "variationListStr",
}


def test_save_payload_keys_match_captured_payload():
    """载荷字段必须与抓到的真实载荷**完全一致**——多一个少一个都可能被判转换异常。"""
    payload = DxmClient.build_save_payload({}, op=1)
    assert set(payload) == RECORDED_SAVE_KEYS
    assert payload["op"] == 1


def test_save_payload_coerces_ids_to_strings():
    """商品对象里 id 类是 int，提交时必须转字符串（抓包如此）。"""
    product = {
        "id": 220114595100809630,
        "shopId": 9013981,
        "categoryId": 29069,
        "region2Id": 43000000000006,
        "shipmentLimitSecond": 172800,
        "categoryType": 1,
        "productName": "长袖T恤",
        "originFileUrl": None,
        "qualifiedEn": None,
    }
    payload = DxmClient.build_save_payload(product, op=2)

    assert payload["id"] == "220114595100809630"      # int → str
    assert payload["shopId"] == "9013981"
    assert payload["region2Id"] == "43000000000006"
    assert payload["shipmentLimitSecond"] == 172800   # 保持数字
    assert payload["categoryType"] == 1
    assert payload["originFileUrl"] == ""             # None → ""
    assert payload["productName"] == "长袖T恤"
    assert payload["op"] == 2


def test_save_payload_survives_missing_and_odd_values():
    payload = DxmClient.build_save_payload({"categoryType": "abc", "shipmentLimitSecond": None})
    assert payload["categoryType"] == 0     # 转不动就 0，不能抛
    assert payload["shipmentLimitSecond"] == 0
    assert payload["productName"] == ""


def test_build_save_archive_matches_captured_format():
    """抓包显示：上传的文件是一个 zip，里面唯一条目是 choiceSave.txt。"""
    product = {"productName": "T-Shirt", "productNameI18n": '{"en":"T-Shirt"}', "op_field": 1}
    blob = DxmClient.build_save_archive(product)

    assert blob[:4] == b"PK\x03\x04"                       # 是 zip
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        assert archive.namelist() == ["choiceSave.txt"]
        info = archive.getinfo("choiceSave.txt")
        assert info.compress_type == zipfile.ZIP_DEFLATED  # 抓包里 method=8
        inner = json.loads(archive.read("choiceSave.txt").decode("utf-8"))
    assert inner["productName"] == "T-Shirt"


def test_save_product_uploads_zip_multipart(sandbox):
    cfg = sandbox["cfg"]
    make_session(cfg)
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "add3f8c2a97" in request.url.path:
            seen["content_type"] = request.headers.get("content-type", "")
            seen["body"] = request.read()
        return httpx.Response(200, json={"code": 0, "msg": "您的产品已保存成功！", "data": "1"}, request=request)

    with DxmClient(cfg, SHOP, transport=httpx.MockTransport(handler)) as client:
        result = client.save_product({"productName": "T-Shirt"}, op=2)

    assert result["code"] == 0
    assert seen["content_type"].startswith("multipart/form-data")
    body = seen["body"]
    assert b'name="file"' in body
    assert b'filename="blob"' in body
    assert b'name="op"' in body
    assert b"PK\x03\x04" in body          # zip 内容确实在 multipart 里
    assert b"choiceSave.txt" in body      # zip 内条目名
    assert b"\r\n2\r\n" in body or body.rstrip().endswith(b"2")   # op=2


def test_save_session_from_context_roundtrip(sandbox):
    cfg = sandbox["cfg"]

    class FakeContext:
        pages: list = []

        def storage_state(self):
            return {
                "cookies": [
                    {"name": "dxm_session", "value": "v", "domain": ".dianxiaomi.com", "path": "/"}
                ],
                "origins": [
                    {"origin": "https://www.dianxiaomi.com", "localStorage": [{"name": "k", "value": "v"}]},
                    {"origin": "https://example.com", "localStorage": [{"name": "x", "value": "y"}]},
                ],
            }

    session = save_session_from_context(cfg, SHOP, FakeContext())
    assert session.cookie_names() == ["dxm_session"]
    assert len(session.storage_origins) == 1          # 只留店小秘的 origin
    assert session_path(cfg, SHOP).exists()
    assert session_path(cfg, SHOP).with_name(f"session_{SHOP.replace(' ', '_')}_localstorage.json").exists()
