"""任务运行器测试：单任务锁、取消的诚实语义、干跑不清理。

全程注入假客户端，**不碰真接口、不碰真表**（真实跑一次会建出真商品）。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from PIL import Image

from temu_bot.config import ShopCfg
from temu_bot.dianxiaomi.client import ShopCheck
from temu_bot.listing import ListingOutcome, ListingPipeline
from temu_bot.sheet.schema import SheetRow, make_row_key
from temu_bot.web.jobs import BusyError, JobRunner, RunRequest

TEMPLATE_NAME = "模板商品"


class FakeClient:
    """最小假客户端：让整条流水线能跑通，但不发任何真实请求。"""

    def __init__(self, *, copy_code: int = 0) -> None:
        self.copy_code = copy_code
        self.calls: list[str] = []
        self.uploads: list[str] = []

    def verify_shop(self, expected):
        return ShopCheck(
            expected=[expected] if isinstance(expected, str) else list(expected),
            ok=True, matched_name="A hat tshirt",
            matched_id="9013981", available=["A hat tshirt"],
        )

    def list_products(self, **kwargs):
        value = str(kwargs.get("search_value") or "")
        if value:
            # 带 shopId：FIND_TEMPLATE 会拿它复核"模板落在这家店"
            return {
                "page": {
                    "list": [
                        {
                            "id": 111,
                            "productName": TEMPLATE_NAME,
                            "dxmState": "online",
                            "shopId": 9013981,
                        }
                    ]
                }
            }
        # 复制后去待发布里找新商品
        return {"page": {"list": [{"id": 222, "productName": TEMPLATE_NAME, "shopId": 9013981}]}}

    def product_edit(self, product_id):
        self.calls.append(f"product_edit({product_id})")
        return {
            "code": 0,
            "data": {
                "product": {
                    "id": product_id,
                    "productName": "旧标题",
                    "productNameI18n": "{}",
                    # 图片字段：真跑要换图，商品里得有可写的位置
                    "mainProductSkuSpecReqs": json.dumps(
                        [{"specId": 3002, "previewImgUrls": "https://old/1.jpg"}],
                        ensure_ascii=False,
                    ),
                    "variationListStr": json.dumps(
                        [{"id": 1, "thumbUrl": "https://old/1.jpg"}], ensure_ascii=False
                    ),
                    "materialImgUrl": "https://old/material.jpg",
                }
            },
        }

    def upload_image(self, path):
        name = Path(path).name
        self.calls.append(f"upload_image({name})")
        self.uploads.append(name)
        return {"cosUrl": f"//cos.example.com/{name}", "fileId": f"/x/{name}"}

    def copy_product(self, product_id, shop_ids):
        self.calls.append(f"copy_product({product_id})")
        return {"code": self.copy_code, "msg": "ok" if self.copy_code == 0 else "米豆不足"}

    def save_product(self, product, op=1, **kwargs):
        self.calls.append(f"save_product(op={op})")
        return {"code": 0, "msg": "ok"}

    def close(self):
        pass


def make_row(tmp_path: Path, index: int) -> SheetRow:
    folder = tmp_path / f"imgs{index}"
    folder.mkdir(exist_ok=True)
    Image.new("RGB", (1340, 1785), (10, 20, 30)).save(folder / "m.jpg", quality=80)
    # 一行 = 主图 + 素材图 这一对；缺素材图的行 PRECHECK 就会拦下来
    Image.new("RGB", (800, 800), (30, 10, 20)).save(folder / "m-素材图.jpg", quality=80)
    return SheetRow(
        row_index=index,
        row_key=make_row_key(str(folder), "m.jpg", "8909735440"),
        folder=str(folder),
        images={"main": ["m.jpg"], "material": ["m-素材图.jpg"]},
        shop="A hat tshirt",
        template_spu="8909735440",
        main_image_index=1,
        title_cn="新标题",
        title_en="New Title",
        raw={9: "m"},          # 第 9 列：SKU 货号前缀编码（上架必填）
    )


@pytest.fixture()
def cfg(sandbox):
    cfg = sandbox["cfg"]
    cfg.shops = [
        ShopCfg(name="A hat tshirt", login="hat-tshirt", expected_dxm_shops=["A hat tshirt"])
    ]
    return cfg


def fake_pipeline_factory(client: FakeClient | None = None):
    """用真流水线 + 假客户端，既不碰网络又测到真实步骤编排。"""
    client = client or FakeClient()

    def factory(cfg_, row, request):
        return ListingPipeline(
            cfg_, row,
            login=request.shop,
            live=request.live,
            op=request.op,
            images_dir=request.images_dir,
            swap_images=request.swap_images,
            client_factory=lambda: client,
        )

    return factory


def make_runner(rows: dict[int, SheetRow] | None = None, **kwargs) -> JobRunner:
    rows = rows or {}
    kwargs.setdefault("pipeline_factory", fake_pipeline_factory())
    return JobRunner(row_loader=lambda i: rows.get(i), **kwargs)


# ------------------------------------------------------------------ 状态机
def test_snapshot_starts_idle():
    snap = JobRunner().snapshot()
    assert snap["state"] == "idle"
    assert snap["busy"] is False
    assert snap["steps"] == []


def test_start_rejects_second_job_while_busy(cfg, tmp_path):
    row = make_row(tmp_path, 2)
    gate = threading.Event()

    class Blocked:
        def __init__(self, cfg_, r, req):
            self.outcome = ListingOutcome(row_key=r.row_key, shop=r.shop)

        def run(self):
            gate.wait(timeout=10)
            return self.outcome

    runner = JobRunner(
        row_loader=lambda i: row,
        pipeline_factory=lambda c, r, req: Blocked(c, r, req),
    )
    runner.start(cfg, RunRequest(shop="hat-tshirt", row_indexes=[2]))
    assert runner.busy

    with pytest.raises(BusyError):
        runner.start(cfg, RunRequest(shop="hat-tshirt", row_indexes=[2]))

    gate.set()
    runner.wait(timeout=10)
    assert not runner.busy


def test_start_rejects_empty_selection(cfg):
    with pytest.raises(BusyError):
        make_runner().start(cfg, RunRequest(shop="hat-tshirt", row_indexes=[]))


# ------------------------------------------------------------------ 正常跑
def test_successful_run_collects_steps_and_result(cfg, tmp_path):
    rows = {2: make_row(tmp_path, 2)}
    runner = make_runner(rows)
    runner.start(cfg, RunRequest(shop="hat-tshirt", row_indexes=[2], live=False))
    runner.wait(timeout=30)

    snap = runner.snapshot()
    assert snap["state"] == "done", snap
    assert snap["finished"] == 1
    assert snap["results"][0]["ok"] is True
    names = [s["name"] for s in snap["steps"]]
    assert names[:3] == ["PRECHECK", "MEDIA", "SHOP_CHECK"]
    assert all("data" in s for s in snap["steps"])


def test_swap_images_reaches_the_pipeline(cfg, tmp_path):
    """`RunRequest.swap_images` 必须真的传到流水线。

    踩过的坑：CLI 那边 `--swap-images` 默认写成了 False，"没给"和"明确不要"分不开，
    新行也被当成已有商品、不传图了。这条钉住同样的错误别在 web 这一层重演。
    """
    rows = {2: make_row(tmp_path, 2)}

    # 默认（None）= 按行自动判断：这一行没有商品ID → 该传图
    auto = FakeClient()
    runner = make_runner(rows, pipeline_factory=fake_pipeline_factory(auto))
    runner.start(cfg, RunRequest(shop="hat-tshirt", row_indexes=[2], live=True))
    runner.wait(timeout=30)
    assert auto.uploads, "新行的图必须真传（默认自动判断）"

    # 明确 False = 就算新行也不传
    off = FakeClient()
    runner = make_runner(rows, pipeline_factory=fake_pipeline_factory(off))
    runner.start(
        cfg, RunRequest(shop="hat-tshirt", row_indexes=[2], live=True, swap_images=False)
    )
    runner.wait(timeout=30)
    assert off.uploads == [], "明确关掉就一张都不许传"


def test_step_dicts_are_json_safe(cfg, tmp_path):
    """步骤 data 里若混进 Path 之类，接口不能因此 500。"""
    rows = {2: make_row(tmp_path, 2)}
    runner = make_runner(rows)
    runner.start(cfg, RunRequest(shop="hat-tshirt", row_indexes=[2]))
    runner.wait(timeout=30)

    json.dumps(runner.snapshot(), ensure_ascii=False)   # 不抛就是安全的


def test_run_writes_a_durable_record(cfg, tmp_path):
    """主流程不回写表格，product_id 只存在这个账本里——没有它重跑就是重复铺货。"""
    rows = {2: make_row(tmp_path, 2)}
    runner = make_runner(rows)
    runner.start(cfg, RunRequest(shop="hat-tshirt", row_indexes=[2]))
    runner.wait(timeout=30)

    path = cfg.data_dir / "state" / "runs.jsonl"
    assert path.exists()
    record = json.loads(path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert record["row_index"] == 2
    assert record["shop"] == "A hat tshirt"
    assert record["live"] is False
    assert "PRECHECK" in record["steps"]


def test_exploding_pipeline_is_reported_not_crashing(cfg, tmp_path):
    def boom(cfg_, row, request):
        raise RuntimeError("假装接口炸了")

    runner = JobRunner(row_loader=lambda i: make_row(tmp_path, 2), pipeline_factory=boom)
    runner.start(cfg, RunRequest(shop="hat-tshirt", row_indexes=[2]))
    runner.wait(timeout=30)

    snap = runner.snapshot()
    assert snap["finished"] == 1
    assert "假装接口炸了" in snap["results"][0]["error"]


# ------------------------------------------------------------------ 取消
def test_cancel_between_rows_stops_before_starting_the_next(cfg, tmp_path):
    rows = {i: make_row(tmp_path, i) for i in (2, 3, 4)}
    runner = make_runner(rows)
    started: list[int] = []
    base = fake_pipeline_factory()

    def factory(cfg_, row, request):
        started.append(row.row_index)
        if row.row_index == 2:
            runner._cancel.set()          # 第一行跑完就请求取消
        return base(cfg_, row, request)

    runner._pipeline_factory = factory
    runner.start(cfg, RunRequest(shop="hat-tshirt", row_indexes=[2, 3, 4]))
    runner.wait(timeout=30)

    assert runner.snapshot()["state"] == "cancelled"
    assert started == [2], "取消后不该再开始新的行"


def test_cancel_message_is_honest_when_idle():
    assert "没有在跑" in JobRunner().request_cancel()


def test_cancel_message_says_current_row_will_finish(cfg, tmp_path):
    gate = threading.Event()

    class Blocked:
        def __init__(self, cfg_, r, req):
            self.outcome = ListingOutcome(row_key=r.row_key, shop=r.shop)

        def run(self):
            gate.wait(timeout=10)
            return self.outcome

    runner = JobRunner(
        row_loader=lambda i: make_row(tmp_path, 2),
        pipeline_factory=lambda c, r, req: Blocked(c, r, req),
    )
    runner.start(cfg, RunRequest(shop="hat-tshirt", row_indexes=[2]))

    msg = runner.request_cancel()
    assert "跑完" in msg, "必须说清当前这一行不会被中断"
    assert runner.snapshot()["cancel_requested"] is True

    gate.set()
    runner.wait(timeout=10)


# ------------------------------------------------------------------ 清理
def test_clean_never_happens_on_dry_run(cfg, tmp_path):
    rows = {2: make_row(tmp_path, 2)}
    cleaned: list[str] = []
    runner = make_runner(rows, on_clean=lambda c, key: cleaned.append(key))

    runner.start(cfg, RunRequest(shop="hat-tshirt", row_indexes=[2], clean_images=True, live=False))
    runner.wait(timeout=30)
    assert cleaned == [], "干跑绝不能清理"


def test_clean_happens_after_successful_live_run(cfg, tmp_path):
    rows = {2: make_row(tmp_path, 2)}
    cleaned: list[str] = []
    runner = make_runner(rows, on_clean=lambda c, key: cleaned.append(key))

    runner.start(cfg, RunRequest(shop="hat-tshirt", row_indexes=[2], clean_images=True, live=True))
    runner.wait(timeout=30)

    snap = runner.snapshot()
    assert snap["results"][0]["ok"] is True, snap["results"][0]
    assert cleaned == [rows[2].row_key]


def test_clean_is_skipped_when_the_row_failed(cfg, tmp_path):
    rows = {2: make_row(tmp_path, 2)}
    cleaned: list[str] = []
    runner = JobRunner(
        row_loader=lambda i: rows[i],
        pipeline_factory=fake_pipeline_factory(FakeClient(copy_code=-1)),
        on_clean=lambda c, key: cleaned.append(key),
    )
    runner.start(cfg, RunRequest(shop="hat-tshirt", row_indexes=[2], clean_images=True, live=True))
    runner.wait(timeout=30)

    assert runner.snapshot()["results"][0]["ok"] is False
    assert cleaned == [], "失败了就不该清理，否则现场都没了"


def test_album_cleanup_only_runs_when_publishing(cfg, tmp_path, monkeypatch):
    """op=2 立即发布成功后才清图片空间；op=1 的草稿还引用着图，不能删。"""
    import temu_bot.web.album_clean as ac

    calls: list[dict] = []

    def fake_clean(cfg_, login, urls, **kw):
        calls.append({"login": login, "urls": list(urls)})
        return ac.AlbumCleanResult(found=len(urls), deleted=len(urls))

    monkeypatch.setattr(ac, "clean_uploaded_images", fake_clean)

    rows = {2: make_row(tmp_path, 2)}
    runner = make_runner(rows)
    runner.start(
        cfg,
        RunRequest(shop="hat-tshirt", row_indexes=[2], clean_images=True, live=True, op=2),
    )
    runner.wait(timeout=30)

    assert calls, "立即发布成功后应该清理图片空间"
    assert calls[0]["login"] == "hat-tshirt"
    assert len(calls[0]["urls"]) == 2          # 主图 + 素材图

    calls.clear()
    rows = {2: make_row(tmp_path, 3)}
    runner = make_runner(rows)
    runner.start(
        cfg,
        RunRequest(shop="hat-tshirt", row_indexes=[2], clean_images=True, live=True, op=1),
    )
    runner.wait(timeout=30)
    assert calls == [], "仅保存的草稿还引用着图，不能删"


def test_real_clean_removes_only_that_rows_work_dir(cfg, tmp_path):
    rows = {2: make_row(tmp_path, 2)}
    other = cfg.work_dir / "别的行" / "out"
    other.mkdir(parents=True)
    (other / "keep.jpg").write_bytes(b"x")

    runner = make_runner(rows)     # 不注入 on_clean → 走真实删除
    runner.start(cfg, RunRequest(shop="hat-tshirt", row_indexes=[2], clean_images=True, live=True))
    runner.wait(timeout=30)

    assert not (cfg.work_dir / rows[2].row_key).exists()
    assert (other / "keep.jpg").exists(), "别的行的产物不能被误删"
