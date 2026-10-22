"""控制台接口测试：只测装配与只读端点，不真起服务、不真跑上架。

`TestClient` 默认 Host 是 `testserver`，会被我们的"只允许本机"中间件挡掉，
所以一律用 `base_url="http://127.0.0.1"`。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from temu_bot.web.app import LOCAL_HOSTNAMES, STATIC_DIR, AppState, create_app
from temu_bot.web.jobs import JobRunner
from temu_bot.web.logs import LogBuffer

BASE = "http://127.0.0.1"


@pytest.fixture()
def client(sandbox, monkeypatch):
    from tests.conftest import make_table

    cfg = sandbox["cfg"]
    monkeypatch.setenv("TEMU_BOT_CONFIG", str(cfg.root / "config" / "config.yaml"))

    logs = LogBuffer()
    state = AppState(cfg=cfg, logs=logs, runner=JobRunner())
    app = create_app(state=state)
    with TestClient(app, base_url=BASE) as c:
        c.temu_state = state
        yield c


# ------------------------------------------------------------------ 页面
def test_index_serves_html(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "自动上架" in res.text


def test_static_files_are_mounted(client):
    assert (STATIC_DIR / "app.js").exists()
    assert (STATIC_DIR / "style.css").exists()
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_static_mount_does_not_shadow_api(client):
    """/static 挂载不能把 /api/* 吃掉。"""
    assert client.get("/api/status").status_code == 200


# ------------------------------------------------------------------ 本机限制
def test_foreign_host_is_rejected(sandbox, monkeypatch):
    cfg = sandbox["cfg"]
    logs = LogBuffer()
    app = create_app(state=AppState(cfg=cfg, logs=logs, runner=JobRunner()))
    with TestClient(app, base_url="http://evil.example.com") as c:
        res = c.get("/api/status")
    assert res.status_code == 403
    assert "本机" in res.json()["error"]


def test_foreign_origin_is_rejected(client):
    res = client.get("/api/status", headers={"Origin": "http://evil.example.com"})
    assert res.status_code == 403


def test_local_origin_is_allowed(client):
    res = client.get("/api/status", headers={"Origin": "http://127.0.0.1:8765"})
    assert res.status_code == 200


def test_localhost_names_are_allowed():
    assert "127.0.0.1" in LOCAL_HOSTNAMES
    assert "localhost" in LOCAL_HOSTNAMES


# ------------------------------------------------------------------ 只读接口
def test_status_reports_table_and_shops(client, sandbox):
    data = client.get("/api/status").json()
    assert data["table_rows"] == 2            # 沙箱表里有 2 行有效数据
    assert data["job"]["state"] == "idle"
    assert "table" in data


def test_rows_endpoint_lists_data_rows(client):
    data = client.get("/api/rows?limit=10").json()
    assert data["error"] == ""
    assert len(data["rows"]) == 2
    assert {"row", "shop", "status", "title_cn"} <= set(data["rows"][0])
    # 沙箱表第 4 行只有文件夹、没有主图 → 空白行，读表时跳过，但要报给前端
    assert data["dropped"] == [4]
    assert data["dropped_count"] == 1


def test_config_endpoint_never_leaks_secret_values(client, monkeypatch):
    monkeypatch.setenv("TEMU_BOT_AI_ZHIPU_KEY", "top-secret-xyz")
    res = client.get("/api/config")
    assert res.status_code == 200
    assert "top-secret-xyz" not in res.text
    assert "project" in res.json()


def test_fs_list_endpoint_returns_entries(client, tmp_path: Path):
    (tmp_path / "a.jpg").write_bytes(b"x")
    data = client.get(f"/api/fs/list?path={tmp_path.as_posix()}&images=true").json()
    assert data["error"] == ""
    assert [f["name"] for f in data["files"]] == ["a.jpg"]


def test_fs_list_without_path_lists_drives(client):
    data = client.get("/api/fs/list").json()
    assert data["error"] == ""


# ------------------------------------------------------------------ 前置校验
def test_preflight_reports_missing_images(client):
    """表里写的图在文件夹里找不到 → 必须被挡下来。"""
    data = client.post("/api/preflight", json={"images_dir": "", "skip_done": False}).json()
    assert data["ok"] is False
    assert data["blocked_total"] >= 1
    assert data["summary"]


# ------------------------------------------------------------------ 行的图片
def test_row_images_reports_resolution_per_slot(client):
    data = client.get("/api/row/2/images").json()
    assert data["row_index"] == 2
    assert data["items"], "至少要列出主图"
    main = data["items"][0]
    assert {"slot", "action", "found", "problems", "target"} <= set(main)
    # 沙箱表指向的 C:/tmp/shop_a 不存在 → 应该如实报"找不到"
    assert data["folder_exists"] is False
    assert main["found"] is False
    assert any("找不到" in p for p in main["problems"])


def test_row_images_on_missing_row_is_404(client):
    assert client.get("/api/row/9999/images").status_code == 404


def test_row_thumb_is_404_with_reason_when_image_missing(client):
    res = client.get("/api/row/2/thumb?slot=main")
    assert res.status_code == 404
    assert "error" in res.json()


def test_row_thumb_serves_a_scaled_jpeg(client, sandbox, tmp_path):
    """把沙箱表指向真有图的目录，缩略图应该能出来，且宽度被缩到要求值。"""
    import io

    from PIL import Image

    folder = tmp_path / "real_imgs"
    folder.mkdir()
    Image.new("RGB", (1340, 1785), (200, 30, 30)).save(folder / "m.jpg", quality=90)
    (folder / "mat.jpg").parent.mkdir(exist_ok=True)
    Image.new("RGB", (800, 800), (30, 30, 200)).save(folder / "mat.jpg", quality=90)

    cfg = sandbox["cfg"]
    from temu_bot.sheet.guard import write_updates
    from tests.conftest import make_table

    table = make_table(
        cfg.root / "表2.xlsx",
        [{1: str(folder), 2: "m.jpg", 8: "mat.jpg", 10: "A lovely costume", 11: "8909735440",
          17: "标题", 18: "Title"}],
    )
    cfg.project.table = table

    data = client.get("/api/row/2/images").json()
    assert data["folder_exists"] is True
    assert data["items"][0]["found"] is True

    res = client.get("/api/row/2/thumb?slot=main&w=200")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/jpeg"
    with Image.open(io.BytesIO(res.content)) as im:
        assert im.width == 200
        assert im.height > im.width        # 竖图比例保留


# ------------------------------------------------------------------ 任务
@pytest.fixture()
def shop_check_ok(monkeypatch):
    """单测不真的去问店小秘：默认让「店铺核对」直接通过。

    `precheck_shops` 本身的行为见 `tests/test_shopcheck.py`。
    """
    from temu_bot.web import app as web_app

    monkeypatch.setattr(
        web_app,
        "precheck_shops",
        lambda cfg, rows: {"ok": True, "message": "测试里直接通过", "checks": []},
    )


def test_run_without_rows_is_rejected(client):
    res = client.post("/api/run", json={"shop": "hat-tshirt", "rows": []})
    assert res.status_code == 409
    assert "没选" in res.json()["error"]


def test_run_is_blocked_before_anything_when_shop_check_fails(client, monkeypatch):
    """店铺核对没过 → **一行都不跑**，直接把核对结果带回来。"""
    from temu_bot.web import app as web_app

    failed = {
        "ok": False,
        "message": "⛔ 店铺核对没通过，还没有开始上架：\n· 第 2 行：表里要上「X」…",
        "checks": [{"shop": "X", "rows": [2], "ok": False, "login": "", "shop_id": "",
                    "matched_name": "", "message": "没有这家店"}],
    }
    monkeypatch.setattr(web_app, "precheck_shops", lambda cfg, rows: failed)

    res = client.post("/api/run", json={"rows": [2], "live": True})
    assert res.status_code == 409
    body = res.json()
    assert body["shop_check"]["ok"] is False
    assert body["started"] is False
    assert client.temu_state.runner.state.value == "idle"    # 没有起任务


def test_diagnose_api_route(client, monkeypatch):
    import temu_bot.diagnose as diag
    from temu_bot.diagnose import ApiCheck, ApiReport

    monkeypatch.setattr(diag, "pick_login", lambda cfg, wanted="": "hat-tshirt")
    monkeypatch.setattr(
        diag,
        "check_api",
        lambda cfg, login, deep=False, **kw: ApiReport(
            login=login, checks=[ApiCheck("登录态 userIn", True, "ok")]
        ),
    )
    monkeypatch.setattr(diag, "write_report", lambda cfg, report: Path("x"))

    data = client.post("/api/diagnose/api", json={"rows": []}).json()
    assert data["ok"] is True
    assert data["reports"][0]["login"] == "hat-tshirt"


def test_run_is_blocked_when_api_check_fails(client, monkeypatch, shop_check_ok):
    """店小秘接口变了 → 开跑前就停，一行都不跑。"""
    import temu_bot.diagnose as diag
    from temu_bot.diagnose import ApiCheck, ApiReport

    monkeypatch.setattr(diag, "pick_login", lambda cfg, wanted="": "hat-tshirt")
    monkeypatch.setattr(
        diag,
        "check_api",
        lambda *a, **k: ApiReport(
            login="hat-tshirt",
            checks=[ApiCheck("商品列表 pageList", False, "疑似接口变更：HTTP 404")],
        ),
    )
    monkeypatch.setattr(diag, "write_report", lambda *a, **k: Path("x"))

    res = client.post("/api/run", json={"rows": [2], "live": True})
    assert res.status_code == 409
    body = res.json()
    assert body["api_check"]["ok"] is False
    assert body["started"] is False
    assert client.temu_state.runner.state.value == "idle"


def test_shops_precheck_endpoint_reports_result(client, monkeypatch):
    from temu_bot.web import app as web_app

    monkeypatch.setattr(
        web_app,
        "precheck_shops",
        lambda cfg, rows: {"ok": True, "message": "通过", "checks": [{"shop": "X", "rows": [2], "ok": True}]},
    )
    res = client.post("/api/shops/precheck", json={"rows": [2]})
    assert res.status_code == 200
    assert res.json()["ok"] is True


def test_live_run_on_a_new_row_is_allowed_now_that_upload_works(sandbox, shop_check_ok):
    """新行可以真跑了。

    图片上传接通之前这里是被硬拦的（复制出来只改标题、图还是模板的，会得到错商品）。
    现在换图会真的发生；就算换图写不进去，流水线自己会在 APPLY 那步 FAIL 中止，
    而不是存下一个"标题对、图片是模板的"商品——所以接口层不再需要这道粗闸门。

    这里仍然**不真跑**：注入一个空流水线，只验证接口放不放行。
    """
    from tests.conftest import make_table
    from temu_bot.listing import ListingOutcome

    cfg = sandbox["cfg"]
    cfg.project.table = make_table(
        cfg.root / "新行表.xlsx",
        [{1: "C:/tmp/x", 2: "a.jpg", 10: "A lovely costume", 11: "8909735440", 17: "标题", 18: "Title"}],
    )

    class Noop:
        def __init__(self, cfg_, row, request):
            self.outcome = ListingOutcome(row_key=row.row_key, shop=row.shop)

        def run(self):
            return self.outcome

    runner = JobRunner(row_loader=lambda i: None, pipeline_factory=lambda c, r, q: Noop(c, r, q))
    app = create_app(state=AppState(cfg=cfg, logs=LogBuffer(), runner=runner))
    with TestClient(app, base_url=BASE) as c:
        res = c.post("/api/run", json={"shop": "lovely", "rows": [2], "live": True})
        assert res.status_code == 200, res.json()
        runner.wait(timeout=20)


def test_dry_run_on_a_new_row_is_still_allowed(client, sandbox, shop_check_ok):
    """干跑不受这条闸限制——干跑不写数据，正是用来看清风险的。"""
    from tests.conftest import make_table

    cfg = sandbox["cfg"]
    cfg.project.table = make_table(
        cfg.root / "新行表2.xlsx",
        [{1: "C:/tmp/x", 2: "a.jpg", 10: "A lovely costume", 11: "8909735440", 17: "标题", 18: "Title"}],
    )
    res = client.post("/api/run", json={"shop": "lovely", "rows": [2], "live": False})
    assert res.status_code == 200
    client.temu_state.runner.wait(timeout=20)


def test_live_run_allowed_when_the_row_already_has_a_product_id(sandbox, monkeypatch, shop_check_ok):
    """已有商品ID 的行续跑是安全的（不复制、不改图），不该被拦。"""
    from temu_bot.web.app import AppState, create_app
    from temu_bot.web.jobs import JobRunner
    from temu_bot.web.logs import LogBuffer

    cfg = sandbox["cfg"]

    class Noop:
        def __init__(self, cfg_, row, request):
            from temu_bot.listing import ListingOutcome

            self.outcome = ListingOutcome(row_key=row.row_key, shop=row.shop)

        def run(self):
            return self.outcome

    runner = JobRunner(row_loader=lambda i: None, pipeline_factory=lambda c, r, q: Noop(c, r, q))
    app = create_app(state=AppState(cfg=cfg, logs=LogBuffer(), runner=runner))
    with TestClient(app, base_url=BASE) as c:
        # 沙箱表第 2 行带着 product_id
        res = c.post("/api/run", json={"shop": "lovely", "rows": [2], "live": True})
        assert res.status_code == 200, res.json()
        runner.wait(timeout=20)


def test_job_snapshot_endpoint(client):
    data = client.get("/api/jobs/current").json()
    assert data["state"] == "idle"
    assert data["busy"] is False


def test_one_failed_row_does_not_stop_the_batch(sandbox):
    """某一行跑挂了，只记这一行的错，**后面的行照常跑**。

    用户要求：表里的一行有问题不要打断整批上架。
    """
    from temu_bot.listing import ListingOutcome
    from temu_bot.web.jobs import JobRunner, RunRequest

    class Boom:
        def __init__(self, cfg_, row, request):
            self.row = row

        def run(self):
            if self.row.row_index == 2:
                raise RuntimeError("这一行故意跑挂")
            return ListingOutcome(row_key=self.row.row_key, shop=self.row.shop)

    runner = JobRunner(pipeline_factory=lambda c, r, q: Boom(c, r, q))
    runner.start(sandbox["cfg"], RunRequest(row_indexes=[2, 3], live=False))
    runner.wait(timeout=20)

    assert runner.state.value == "done"
    by_row = {r.row_index: r for r in runner.results}
    assert sorted(by_row) == [2, 3]
    assert by_row[2].error                     # 第 2 行记下错误
    assert by_row[3].ok                        # 第 3 行照常跑完


def test_cancel_when_idle_is_honest(client):
    data = client.post("/api/jobs/cancel").json()
    assert "没有在跑" in data["message"]


# ------------------------------------------------------------------ 系统文件对话框
def test_dialog_folder_returns_the_picked_path(client, monkeypatch):
    """浏览器拿不到本地路径，所以由后端弹系统对话框再回传。"""
    import temu_bot.web.dialogs as dialogs

    monkeypatch.setattr(dialogs, "pick_folder", lambda **kw: r"C:\Users\A\Desktop\Temu")
    res = client.post("/api/dialog/folder", json={"title": "选择素材文件夹"})
    assert res.status_code == 200
    assert res.json() == {"path": r"C:\Users\A\Desktop\Temu", "cancelled": False}


def test_dialog_folder_reports_cancel(client, monkeypatch):
    import temu_bot.web.dialogs as dialogs

    monkeypatch.setattr(dialogs, "pick_folder", lambda **kw: "")
    data = client.post("/api/dialog/folder", json={}).json()
    assert data["cancelled"] is True
    assert data["path"] == ""


def test_dialog_returns_409_when_one_is_already_open(client, monkeypatch):
    import temu_bot.web.dialogs as dialogs

    def busy(**kw):
        raise dialogs.DialogBusy("已经有一个文件对话框开着")

    monkeypatch.setattr(dialogs, "pick_folder", busy)
    res = client.post("/api/dialog/folder", json={})
    assert res.status_code == 409
    assert "已经有一个" in res.json()["error"]


def test_dialog_surfaces_a_failure_instead_of_hanging(client, monkeypatch):
    import temu_bot.web.dialogs as dialogs

    def boom(**kw):
        raise RuntimeError("文件对话框调用失败（退出码 1）：xxx")

    monkeypatch.setattr(dialogs, "pick_file", boom)
    res = client.post("/api/dialog/file", json={})
    assert res.status_code == 500
    assert "调用失败" in res.json()["error"]


def test_dialog_file_passes_the_filter_through(client, monkeypatch):
    import temu_bot.web.dialogs as dialogs

    seen = {}

    def fake(**kw):
        seen.update(kw)
        return r"C:\x\表.xlsx"

    monkeypatch.setattr(dialogs, "pick_file", fake)
    client.post("/api/dialog/file", json={"title": "选择商品信息表", "filters": "Excel 表格|*.xlsx"})
    assert seen["filter_spec"] == "Excel 表格|*.xlsx"
    assert seen["title"] == "选择商品信息表"


def test_dialog_save_endpoint(client, monkeypatch):
    import temu_bot.web.dialogs as dialogs

    monkeypatch.setattr(dialogs, "save_file", lambda **kw: r"C:\out\新表.xlsx")
    data = client.post("/api/dialog/save", json={}).json()
    assert data["path"] == r"C:\out\新表.xlsx"


def test_dialog_endpoints_are_blocked_for_foreign_origins(client):
    """别的网页不能诱导本机弹对话框。"""
    res = client.post("/api/dialog/folder", json={}, headers={"Origin": "http://evil.example.com"})
    assert res.status_code == 403


# ------------------------------------------------------------------ 收集图片
@pytest.fixture()
def collect_dir(tmp_path):
    from PIL import Image

    root = tmp_path / "素材"
    root.mkdir()
    for stem in ("11111111111", "22222222222"):
        Image.new("RGB", (1340, 1785), (20, 40, 60)).save(root / f"{stem}.jpg", quality=85)
        Image.new("RGB", (800, 800), (60, 40, 20)).save(root / f"{stem}-素材图.jpg", quality=85)
    Image.new("RGB", (1340, 1785), (10, 10, 10)).save(root / "99999999999.jpg", quality=85)
    return root


def test_collect_scan_is_read_only(client, collect_dir):
    res = client.post("/api/collect/scan",
                      json={"folder": str(collect_dir), "shop": "A lovely costume",
                            "template_spu": "8909735440", "force_material": False})
    assert res.status_code == 200
    data = res.json()
    assert data["row_count"] == 2
    assert data["skipped_count"] == 1          # 缺素材图那张（--no-force-material 的旧行为）
    assert data["rows"][0]["main"].endswith(".jpg")
    assert data["out_path"].endswith("商品信息表.xlsx")
    # 只读：不能真的写出文件
    assert not (collect_dir / "商品信息表.xlsx").exists()


def test_collect_scan_generates_material_by_default(client, collect_dir):
    """2026-10-16 用户要求：**缺素材图默认用主图生成一张**，不再默认跳过。"""
    data = client.post("/api/collect/scan",
                       json={"folder": str(collect_dir), "shop": "A lovely costume",
                             "template_spu": "8909735440"}).json()
    assert data["row_count"] == 3, "缺素材图那张也该收进来"
    assert data["skipped_count"] == 0


def test_collect_run_requires_confirmation(client, collect_dir):
    res = client.post("/api/collect/run",
                      json={"folder": str(collect_dir), "shop": "X", "live": False})
    assert res.status_code == 400
    assert "确认" in res.json()["error"]
    assert not (collect_dir / "商品信息表.xlsx").exists()


def test_collect_run_writes_the_table(client, collect_dir):
    res = client.post("/api/collect/run",
                      json={"folder": str(collect_dir), "shop": "A lovely costume",
                            "template_spu": "8909735440", "force_material": False,
                            "live": True})
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["rows"] == 2

    import openpyxl

    wb = openpyxl.load_workbook(data["path"], read_only=True)
    rows = list(wb["Sheet1"].iter_rows(values_only=True))
    wb.close()
    assert rows[0][9] == "店铺名"
    assert rows[1][9] == "A lovely costume"


def test_collect_never_overwrites_an_existing_table(client, collect_dir):
    existing = collect_dir / "商品信息表.xlsx"
    existing.write_bytes("原有的表".encode("utf-8"))
    before = existing.read_bytes()

    res = client.post("/api/collect/run",
                      json={"folder": str(collect_dir), "shop": "X",
                            "force_material": False, "live": True})
    data = res.json()

    assert data["renamed"] is True
    assert data["path"] != str(existing)
    assert existing.read_bytes() == before


def test_collect_scan_reports_spec_problems(client, tmp_path):
    """主图尺寸不合规要在扫描预览里就说出来，别等上架被平台打回。"""
    from PIL import Image

    root = tmp_path / "规格素材"
    root.mkdir()
    Image.new("RGB", (1340, 1784), (5, 5, 5)).save(root / "bad.jpg", quality=85)   # 矮 1 像素
    Image.new("RGB", (800, 800), (5, 5, 5)).save(root / "bad-素材图.jpg", quality=85)

    data = client.post("/api/collect/scan",
                       json={"folder": str(root), "shop": "A lovely costume"}).json()

    assert data["row_count"] == 1
    assert data["spec_problem_count"] == 1
    assert data["rows"][0]["problems"]
    assert any("高" in p for p in data["rows"][0]["problems"])


def _with_shop(sandbox, name="A lovely costume"):
    from temu_bot.config import ShopCfg

    sandbox["cfg"].shops = [ShopCfg(name=name, login="lovely", expected_dxm_shops=[name])]
    return name


def test_collect_scan_warns_about_an_unknown_shop(client, collect_dir, sandbox):
    """店铺名写错会一路错到上架，扫描时就提醒。"""
    _with_shop(sandbox)
    data = client.post("/api/collect/scan",
                       json={"folder": str(collect_dir), "shop": "打错字的店名"}).json()
    assert "不在配置的店铺列表里" in data["unknown_shop"]
    assert data["known_shops"] == ["A lovely costume"]


def test_collect_scan_is_silent_for_a_known_shop(client, collect_dir, sandbox):
    known = _with_shop(sandbox)
    data = client.post("/api/collect/scan",
                       json={"folder": str(collect_dir), "shop": known}).json()
    assert data["unknown_shop"] == ""


def test_collect_scan_without_folder_is_rejected(client):
    res = client.post("/api/collect/scan", json={"folder": ""})
    assert res.status_code == 400


def test_collect_scan_fills_titles_from_the_list(client, collect_dir, tmp_path):
    """选了「图片文件名清单」就按 11 位 ID 把中英文标题带进预览。"""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["数字前缀", "中文后缀 "])
    ws.append(["11111111111", "中文一"] + [""] * 7 + ["English One"])
    lst = tmp_path / "图片文件名清单.xlsx"
    wb.save(lst)

    data = client.post(
        "/api/collect/scan",
        json={
            "folder": str(collect_dir),
            "shop": "A lovely costume",
            "title_list": str(lst),
        },
    ).json()

    by_stem = {r["stem"]: r for r in data["rows"]}
    assert by_stem["11111111111"]["title_cn"] == "中文一"
    assert by_stem["11111111111"]["title_en"] == "English One"
    assert by_stem["22222222222"]["title_cn"] == ""
    assert data["title_list"]["ok"] is True
    assert data["title_list"]["matched"] == 1


def test_collect_scan_on_empty_folder_reports_zero(client, tmp_path):
    empty = tmp_path / "空的"
    empty.mkdir()
    data = client.post("/api/collect/scan", json={"folder": str(empty)}).json()
    assert data["row_count"] == 0
    assert data["skipped_count"] == 1


# ------------------------------------------------------------------ 工具箱
@pytest.fixture()
def tool_dir(tmp_path):
    from PIL import Image

    folder = tmp_path / "工具箱素材"
    folder.mkdir()
    Image.new("RGB", (20, 20), (9, 9, 9)).save(folder / "裙子.jpg")
    Image.new("RGB", (20, 20), (8, 8, 8)).save(folder / "帽子.png")
    return folder


def test_toolbox_probe_reports_the_ledger(client, sandbox):
    data = client.get("/api/toolbox/probe").json()
    assert data["pillow"]["avif"] is True
    assert data["ledger"]["merged_total"] == 0
    assert str(sandbox["cfg"].toolbox_ledger) == data["ledger"]["ledger"]


def test_toolbox_scan_writes_nothing(client, tool_dir):
    before = sorted(p.name for p in tool_dir.iterdir())
    data = client.post(
        "/api/toolbox/rename/scan",
        json={"folder": str(tool_dir), "mode": "id-prefix"},
    ).json()

    assert data["counts"]["would"] == 2
    assert sorted(p.name for p in tool_dir.iterdir()) == before, "预览不该动文件"
    assert not (tool_dir / "图片文件名清单.csv").exists()


def test_toolbox_run_needs_an_explicit_confirm(client, tool_dir):
    """少了 live 就不许写 —— 跟收集/上架那边一个规矩。"""
    for tool in ("rename", "export", "convert", "resize", "merge"):
        res = client.post(f"/api/toolbox/{tool}/run", json={"folder": str(tool_dir)})
        assert res.status_code == 400, tool
        assert "确认" in res.json()["error"]
    assert sorted(p.name for p in tool_dir.iterdir()) == ["帽子.png", "裙子.jpg"]


def test_toolbox_rename_run_actually_renames(client, tool_dir):
    import re

    data = client.post(
        "/api/toolbox/rename/run",
        json={"folder": str(tool_dir), "mode": "id-prefix", "live": True},
    ).json()

    assert data["ok"] is True, data
    after = sorted(p.name for p in tool_dir.iterdir())
    assert all(re.fullmatch(r"\d{11}-(裙子\.jpg|帽子\.png)", n) for n in after), after


def test_toolbox_export_run_writes_both_files(client, tool_dir, sandbox):
    data = client.post(
        "/api/toolbox/export/run",
        json={"folder": str(tool_dir), "fmt": "both", "live": True},
    ).json()

    assert data["ok"] is True, data
    csv = tool_dir / "图片文件名清单.csv"
    assert csv.is_file() and (tool_dir / "图片文件名清单.xlsx").is_file()
    assert csv.read_bytes().decode("gbk").startswith('"数字前缀","中文后缀" ')
    assert sorted(data["outputs"]) == sorted([str(csv), str(tool_dir / "图片文件名清单.xlsx")])


def test_toolbox_convert_run_makes_the_new_format(client, tool_dir):
    data = client.post(
        "/api/toolbox/convert/run",
        json={"folder": str(tool_dir), "target_format": "webp", "live": True},
    ).json()

    assert data["ok"] is True, data
    assert (tool_dir / "裙子.webp").is_file()
    assert (tool_dir / "帽子.webp").is_file()


def test_toolbox_resize_run_overwrites_in_place(client, tool_dir):
    from PIL import Image

    data = client.post(
        "/api/toolbox/resize/run",
        json={"folder": str(tool_dir), "live": True},
    ).json()

    assert data["ok"] is True, data
    out = tool_dir / "裙子.jpg"
    assert out.is_file(), "JPG 原图应该原地覆盖"
    with Image.open(out) as im:
        assert im.size == (1340, 1785)
    assert (tool_dir / "帽子.jpg").is_file(), "PNG 应该转成同名 JPG"
    assert not (tool_dir / "帽子.png").exists(), "原 PNG 应该被删掉"


def test_toolbox_resize_scan_writes_nothing(client, tool_dir):
    before = sorted(p.name for p in tool_dir.iterdir())
    data = client.post(
        "/api/toolbox/resize/scan",
        json={"folder": str(tool_dir), "width": 800, "height": 800},
    ).json()

    assert data["counts"]["would"] == 2
    assert sorted(p.name for p in tool_dir.iterdir()) == before
    assert all(i["target"].endswith(".jpg") for i in data["items"])
    assert all("_800x800" not in i["target"] for i in data["items"]), "默认保持原名"


def test_toolbox_combo_scan_writes_nothing(client, tool_dir):
    before = sorted(p.name for p in tool_dir.iterdir())
    data = client.post("/api/toolbox/combo/scan", json={"folder": str(tool_dir)}).json()

    assert data["items"], data
    assert sorted(p.name for p in tool_dir.iterdir()) == before


def test_toolbox_combo_start_runs_to_done(client, tool_dir):
    import time

    res = client.post("/api/toolbox/combo/start", json={"folder": str(tool_dir)})
    assert res.status_code == 200, res.text

    snap = res.json()
    for _ in range(200):
        if snap["state"] != "running":
            break
        time.sleep(0.05)
        snap = client.get("/api/toolbox/combo/status").json()

    assert snap["state"] == "done", snap
    assert snap["percent"] == 100
    assert snap["result"]["ok"] is True

    names = sorted(p.name for p in tool_dir.iterdir())
    ids = [n for n in names if n.endswith(".jpg") and n[:-4].isdigit() and len(n[:-4]) == 11]
    assert len(ids) == 2, names
    assert "图片文件名清单.csv" in names
    assert "图片文件名清单.xlsx" in names


def test_toolbox_merge_run_writes_the_merged_book(client, tool_dir):
    from openpyxl import Workbook, load_workbook

    wb = Workbook()
    wb.active.append(["甲", "乙"])
    wb.save(tool_dir / "表.xlsx")

    data = client.post(
        "/api/toolbox/merge/run", json={"folder": str(tool_dir), "live": True}
    ).json()

    assert data["ok"] is True, data
    merged = tool_dir / "MergedExcel.xlsx"
    assert merged.is_file()
    check = load_workbook(merged)
    rows = [list(r) for r in check["Merged"].iter_rows(values_only=True)]
    check.close()
    assert rows == [["甲", "乙"]]


def test_toolbox_rejects_a_folder_that_is_not_there(client, tmp_path):
    res = client.post("/api/toolbox/export/scan", json={"folder": str(tmp_path / "没这个")})
    assert res.status_code == 400
    assert "文件夹不存在" in res.json()["error"]


def test_toolbox_export_with_no_format_chosen_is_a_clear_error(client, tool_dir):
    data = client.post(
        "/api/toolbox/export/scan",
        json={"folder": str(tool_dir), "fmt": "", "csv_path": ""},
    ).json()
    assert data["ok"] is False
    assert "至少勾选一种" in data["items"][0]["detail"]


# ------------------------------------------------------------------ 日志
def test_logs_polling_endpoint(client):
    state = client.temu_state
    state.logs.buffer.add(level="INFO", logger="t", msg="第一条")
    state.logs.buffer.add(level="WARNING", logger="t", msg="第二条")

    data = client.get("/api/logs?after=0").json()
    assert [e["msg"] for e in data["entries"]] == ["第一条", "第二条"]
    assert data["latest_seq"] == 2

    incremental = client.get("/api/logs?after=2").json()
    assert incremental["entries"] == []


def test_log_stream_route_is_registered_as_event_stream(client):
    """**不走 TestClient 请求这条路由**——它是无限流，请求会把测试挂死
    （关闭流时要等一个永远不会结束的生成器）。所以只断言它注册成了 SSE 响应。

    真正的推送逻辑在下面用 log_event_stream 直接测。
    """
    from fastapi.responses import StreamingResponse

    import asyncio

    route = next(
        (r for r in client.app.routes if getattr(r, "path", "") == "/api/logs/stream"),
        None,
    )
    assert route is not None, "SSE 路由没注册上"

    class FakeRequest:
        def __init__(self, app):
            self.app = app
            self.headers = {}

        async def is_disconnected(self):
            return True

    response = asyncio.run(route.endpoint(FakeRequest(client.app), after=0))
    assert isinstance(response, StreamingResponse)
    assert str(response.media_type).startswith("text/event-stream")


# ------------------------------------------------------------------ SSE 续传与推送
def test_initial_cursor_prefers_last_event_id():
    """重连时必须认 Last-Event-ID，否则每次重连都把整段日志重放一遍。"""
    from temu_bot.web.app import initial_cursor

    assert initial_cursor("42", 0) == 42
    assert initial_cursor("42", 7) == 42          # 头部优先于 URL 参数
    assert initial_cursor(None, 7) == 7
    assert initial_cursor("", 7) == 7
    assert initial_cursor("不是数字", 7) == 7
    assert initial_cursor(None, -5) == 0


def _collect_until_disconnect(entries_to_push):
    """把日志流跑固定圈数就断开——不用 sleep，不依赖时序。"""
    import asyncio

    from temu_bot.web.app import log_event_stream
    from temu_bot.web.logs import RingBuffer

    buffer = RingBuffer()
    calls = {"n": 0}

    async def is_disconnected():
        calls["n"] += 1
        # 第一圈放行、第二圈推送、第三圈断开
        if calls["n"] >= 3:
            return True
        if calls["n"] == 2:
            for msg in entries_to_push:
                buffer.add(level="INFO", logger="t", msg=msg)
        return False

    async def run():
        out = []
        async for chunk in log_event_stream(buffer, after=0, is_disconnected=is_disconnected, poll=0):
            out.append(chunk)
        return out

    return asyncio.run(run())


def test_log_stream_pushes_entries_with_ids_and_stops_on_disconnect():
    chunks = _collect_until_disconnect(["第一条", "第二条"])
    body = "".join(chunks)

    assert body.startswith("retry: 3000")
    assert "id: 1" in body and "id: 2" in body
    assert "第一条" in body and "第二条" in body
    assert body.count(": ping") >= 1      # 空闲时要有心跳


def test_log_stream_data_is_json_encoded_so_newlines_are_safe():
    """日志里换行会截断 SSE 帧，所以必须 JSON 编码后再放进 data:。

    每个 chunk 形如 "id: N\\ndata: {...}\\n\\n"，id 与 data 在同一块里，
    所以先按行拆再找 data 行。
    """
    import json as _json

    chunks = _collect_until_disconnect(["第一行\n第二行"])
    lines = "".join(chunks).splitlines()
    data_lines = [ln for ln in lines if ln.startswith("data: ")]
    assert data_lines, chunks

    payload = data_lines[0][len("data: "):]
    assert "\n" not in payload, "data 里出现裸换行会截断 SSE 帧"
    assert _json.loads(payload)["msg"] == "第一行\n第二行"
