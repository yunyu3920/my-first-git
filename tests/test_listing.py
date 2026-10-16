"""上架主流程测试：用假客户端钉住"干跑不写、店铺不符就中止"这两条底线。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from temu_bot.config import ShopCfg
from temu_bot.dianxiaomi.client import ShopCheck
from temu_bot.listing import ListingPipeline, Status
from temu_bot.sheet.schema import SheetRow, make_row_key

# 夹具那个 row 的第 9 列「一键生成SKU货号前缀编码」
SKU_CODE = "00742093597"
# 夹具里「A hat tshirt」的店小秘 shopId（verify_shop 也返回它）
DEFAULT_SHOP_ID = "9013981"


class FakeClient:
    """只实现流程会用到的方法，并记录调用。"""

    def __init__(
        self,
        *,
        shops: list[str] | None = None,
        template_row: dict | None = None,
        product: dict | None = None,
        offline_rows: list[dict] | None = None,
        copy_code: int = 0,
        save_code: int = 0,
        copy_adds: dict | None = None,
        template_product: dict | None = None,
    ) -> None:
        self.shops = shops if shops is not None else ["A hat tshirt"]
        # 真实商品列表里每行都带 shopId；FIND_TEMPLATE 会拿它复核"模板落在这家店"，
        # 所以假模板也得带上（默认就跟 verify_shop 返回的那个 shopId 一致）
        self.template_row = template_row
        if self.template_row is not None and "shopId" not in self.template_row:
            self.template_row = {**self.template_row, "shopId": DEFAULT_SHOP_ID}
        self.product = product or {}
        self.offline_rows = offline_rows or []
        self.copy_code = copy_code
        self.save_code = save_code
        # 复制成功后往待发布里"加"的这个商品（模拟真实复制）
        self.copy_adds = copy_adds
        # 模板商品本身（读模板 id 时返回它）。不设就跟 product 一样。
        self.template_product = template_product
        self.calls: list[str] = []
        self.uploads: list[str] = []
        self.saved_product: dict | None = None
        self.saved_op: int | None = None
        self.closed = False

    # --- 流程用到的方法
    def verify_shop(self, expected):
        names = self.shops
        wanted = expected if isinstance(expected, list) else [expected]
        for name in names:
            if name.strip().lower() in [str(w).strip().lower() for w in wanted]:
                return ShopCheck(expected=list(wanted), ok=True, matched_name=name, matched_id="9013981", available=names)
        return ShopCheck(expected=list(wanted), ok=False, available=names)

    def list_products(self, **kwargs):
        value = str(kwargs.get("search_value") or "")
        shop_id = kwargs.get("shop_id")
        self.calls.append(
            f"list_products(search={value or '-'}, shop_id={shop_id if shop_id is not None else '-'})"
        )
        if value:
            # 真接口按 shopId 过滤；这里照做，否则"模板必须在店里"那条测不出来
            rows = []
            for row in [self.template_row] if self.template_row else []:
                if shop_id in (None, -1) or str(row.get("shopId")) == str(shop_id):
                    rows.append(row)
            return {"page": {"list": rows, "totalSize": len(rows)}}
        rows = self.offline_rows
        return {"page": {"list": rows, "totalSize": len(rows)}}

    def product_edit(self, product_id):
        self.calls.append(f"product_edit({product_id})")
        template_id = str((self.template_row or {}).get("id") or "")
        if self.template_product is not None and template_id == str(product_id):
            return {"code": 0, "data": {"product": dict(self.template_product)}}
        return {"code": 0, "data": {"product": dict(self.product)}}

    def copy_product(self, product_id, shop_ids):
        self.calls.append(f"copy_product({product_id},{shop_ids})")
        if self.copy_code == 0 and self.copy_adds is not None:
            # 真复制会把新商品放进待发布：模拟"复制后列表里多了一个"
            self.offline_rows = list(self.offline_rows) + [self.copy_adds]
        return {"code": self.copy_code, "msg": "ok" if self.copy_code == 0 else "米豆不足"}

    def upload_image(self, path):
        from pathlib import Path as _Path

        name = _Path(path).name
        self.calls.append(f"upload_image({name})")
        self.uploads.append(name)
        return {"cosUrl": f"//cos.example.com/{name}", "fileId": f"/x/{name}"}

    def save_product(self, product, op=1, **kwargs):
        self.calls.append(f"save_product(op={op})")
        self.saved_product = product
        self.saved_op = op
        return {"code": self.save_code, "msg": "您的产品已保存成功！", "data": "1"}

    def close(self):
        self.closed = True


# `row` / `cfg_with_shop` 两个夹具在 conftest.py 里（别的测试模块也要用）


def product_with_images(**overrides):
    """一个带图片字段的假商品。

    注意 mainProductSkuSpecReqs / variationListStr 在接口里是 **JSON 字符串**，
    不是对象——换图最容易在这里踩坑，所以假数据也照这个来。

    `extCode` 填的是 row 夹具第 9 列那个值（= "货号已经是对的了"），
    这样"已有商品 + 一切都对 → 不该白写一次"的用例才测得到东西。
    SKC 级的 extCode 留成模板的（真实商品就是这样，我们不动它）。
    """
    product = {
        "productName": "旧标题",
        "productNameI18n": json.dumps({"en": "Old Title"}, ensure_ascii=False),
        "mainProductSkuSpecReqs": json.dumps(
            [
                {
                    "specId": 3002,
                    "specName": "深灰色",
                    "extCode": "ZD001",  # SKC 级：模板的，不该被改
                    "previewImgUrls": "https://old/1.jpg|https://old/2.jpg",
                }
            ],
            ensure_ascii=False,
        ),
        "variationListStr": json.dumps(
            [
                {"id": 1, "thumbUrl": "https://old/1.jpg", "extCode": SKU_CODE},
                {"id": 2, "thumbUrl": "https://old/1.jpg", "extCode": SKU_CODE},
            ],
            ensure_ascii=False,
        ),
        "materialImgUrl": "https://old/material.jpg",
    }
    product.update(overrides)
    return product


def make_pipeline(cfg, row, client, **kwargs):
    return ListingPipeline(cfg, row, login="hat-tshirt", client_factory=lambda: client, **kwargs)


def step_names(outcome, *, without=("WRITEBACK",)):
    """上架步骤的名字。

    默认不含最后的 `WRITEBACK`——那是收尾（把结果写进表格），不属于"上架本身做了什么"。
    这样测流程的用例不会被收尾步骤的变化带崩。
    """
    return [s.name for s in outcome.steps if s.name not in without]


def by_name(outcome, name):
    """按步骤名取那一步（步骤数会随流程演进变，别用下标钉）。"""
    return next(s for s in outcome.steps if s.name == name)


def test_on_step_callback_receives_every_step_in_order(cfg_with_shop, row):
    """Web 控制台的实时进度靠这个回调；顺序必须与 outcome.steps 一致。"""
    seen = []
    client = FakeClient(
        template_row={"id": 111, "productName": "模板商品", "dxmState": "online"},
    )
    outcome = make_pipeline(
        cfg_with_shop, row, client, live=False, on_step=seen.append
    ).run()

    assert [s.name for s in seen] == [s.name for s in outcome.steps]
    assert seen[-1].name == "WRITEBACK"


def test_broken_on_step_callback_does_not_break_the_run(cfg_with_shop, row):
    """回调是旁路：它抛异常不能把上架本身带崩。"""
    def boom(_step):
        raise RuntimeError("控制台炸了")

    client = FakeClient(
        template_row={"id": 111, "productName": "模板商品", "dxmState": "online"},
    )
    outcome = make_pipeline(cfg_with_shop, row, client, live=False, on_step=boom).run()

    assert not outcome.failed
    assert [s.name for s in outcome.steps][-1] == "WRITEBACK"


def test_precheck_fails_when_images_missing(cfg_with_shop, row):
    Path(row.folder, row.main_image).unlink()
    client = FakeClient()
    outcome = make_pipeline(cfg_with_shop, row, client).run()

    assert outcome.failed
    assert "缺图片文件" in outcome.steps[0].detail
    assert client.calls == []          # 预检查没过就不该碰接口


def test_precheck_fails_when_the_row_has_no_material_image(cfg_with_shop, row):
    """缺素材图 = 素材图位会留着模板那张 → 上出来是"半对"的商品，别硬上。"""
    row.images.pop("material", None)
    client = FakeClient()
    outcome = make_pipeline(cfg_with_shop, row, client).run()

    assert outcome.failed
    assert "素材图" in outcome.steps[0].detail
    assert client.calls == []


def test_precheck_fails_when_the_row_has_no_sku_code(cfg_with_shop, row):
    """没有第 9 列就不能跑：沿用模板的货号 = 一批商品共用一个货号，用户明确不要。"""
    row.raw = {}
    client = FakeClient()
    outcome = make_pipeline(cfg_with_shop, row, client).run()

    assert outcome.failed
    assert "SKU货号前缀编码" in outcome.steps[0].detail
    assert client.calls == []


# ------------------------------------------------------------------ SKU 货号
def test_live_run_fills_the_sku_code_from_the_table(cfg_with_shop, row):
    """表里第 9 列 → 每个 SKU 的 extCode；SKC 级的保持模板的不动。"""
    client = FakeClient(
        template_row={"id": 111, "productName": "模板商品", "dxmState": "online"},
        product=product_with_images(
            variationListStr=json.dumps(
                [
                    {"id": 1, "thumbUrl": "https://old/1.jpg", "extCode": "99689509419"},
                    {"id": 2, "thumbUrl": "https://old/1.jpg", "extCode": "99689509419"},
                ],
                ensure_ascii=False,
            )
        ),
        offline_rows=[{"id": 222, "productName": "模板商品"}],
    )
    outcome = make_pipeline(cfg_with_shop, row, client, live=True, op=1).run()

    assert not outcome.failed, [s.line() for s in outcome.steps]
    saved = client.saved_product

    for entry in json.loads(saved["variationListStr"]):
        assert entry["extCode"] == SKU_CODE, "每个 SKU 都要填表里那个货号"

    skc = json.loads(saved["mainProductSkuSpecReqs"])[0]
    assert skc["extCode"] == "ZD001", "SKC 级的 extCode 是模板的，不该被改"
    assert SKU_CODE in by_name(outcome, "APPLY").detail


def test_sku_code_change_alone_forces_a_save(cfg_with_shop, row):
    """标题和图都对、只有货号不对 → 也必须提交，不能因为"没事干"就跳过。"""
    client = FakeClient(
        template_row={"id": 111, "productName": "模板商品", "dxmState": "online"},
        product=product_with_images(
            productName=row.title_cn,
            productNameI18n=json.dumps({"en": row.title_en}),
            variationListStr=json.dumps(
                [{"id": 1, "thumbUrl": "https://old/1.jpg", "extCode": "99689509419"}],
                ensure_ascii=False,
            ),
        ),
        offline_rows=[{"id": 900, "productName": "别的商品"}],
        copy_adds={"id": 222, "productName": "模板商品"},
    )
    outcome = make_pipeline(cfg_with_shop, row, client, live=True, op=1).run()

    assert not outcome.failed, [s.line() for s in outcome.steps]
    assert by_name(outcome, "APPLY").status is Status.OK
    assert client.saved_product is not None
    assert json.loads(client.saved_product["variationListStr"])[0]["extCode"] == SKU_CODE


def test_dry_run_preview_mentions_the_sku_code(cfg_with_shop, row):
    client = FakeClient(
        template_row={"id": 111, "productName": "模板商品", "dxmState": "online"},
        product=product_with_images(
            variationListStr=json.dumps(
                [{"id": 1, "thumbUrl": "https://old/1.jpg", "extCode": "99689509419"}],
                ensure_ascii=False,
            )
        ),
    )
    outcome = make_pipeline(cfg_with_shop, row, client, live=False).run()

    detail = by_name(outcome, "APPLY").detail
    assert "SKU货号" in detail
    assert SKU_CODE in detail
    assert not any("save_product" in c for c in client.calls), "干跑不许真写"


def test_shop_mismatch_aborts_and_never_copies(cfg_with_shop, row):
    """用户明确要求的安全闸：店铺不一致必须停，绝不能继续复制。"""
    row.shop = "A lovely costume"      # 表里是另一家店
    client = FakeClient(shops=["A hat tshirt"])
    outcome = make_pipeline(cfg_with_shop, row, client, live=True).run()

    assert outcome.failed
    assert step_names(outcome) == ["PRECHECK", "MEDIA", "SHOP_CHECK"]
    shop_step = by_name(outcome, "SHOP_CHECK")
    assert "A lovely costume" in shop_step.detail
    assert "A hat tshirt" in shop_step.detail
    assert not any("copy_product" in c for c in client.calls)


def test_live_shop_check_catches_account_mismatch(cfg_with_shop, row):
    """登录账号里压根没有表里要上的这家店 → 停，并列出账号里实际有哪些店。"""
    client = FakeClient(shops=["A lovely costume"])   # 账号里只有另一家店
    outcome = make_pipeline(cfg_with_shop, row, client, live=True).run()

    assert outcome.failed
    assert step_names(outcome) == ["PRECHECK", "MEDIA", "SHOP_CHECK"]
    detail = by_name(outcome, "SHOP_CHECK").detail
    assert "A hat tshirt" in detail
    assert "A lovely costume" in detail, "要把账号里实际有的店列出来，好让人当场判断"
    assert not any("copy_product" in c for c in client.calls)


def test_shop_check_does_not_depend_on_the_config_mapping(cfg_with_shop, row):
    """店铺不固定：**配置里没有这家店也照样能跑**，只要登录账号里有。

    以前的实现是拿配置的 shops 映射去对（表里的店 → 登录身份），
    配置一变或没登记就误判。现在只认实时读到的账号店铺列表。
    """
    cfg_with_shop.shops = []          # 配置里一个店都没登记
    client = FakeClient(
        shops=["A hat tshirt"],
        template_row={"id": 111, "productName": "模板商品", "dxmState": "online"},
        product=product_with_images(),
        offline_rows=[{"id": 900, "productName": "别的商品"}],
        copy_adds={"id": 222, "productName": "模板商品"},
    )
    outcome = make_pipeline(cfg_with_shop, row, client, live=True).run()

    assert not outcome.failed, [s.line() for s in outcome.steps]
    shop_step = by_name(outcome, "SHOP_CHECK")
    assert shop_step.status is Status.OK
    assert shop_step.data["shop_id"] == DEFAULT_SHOP_ID


def test_find_template_must_be_in_the_logged_in_shop(cfg_with_shop, row):
    """模板必须在**这家店**里找得到；搜到的商品属于别家店 → 一律不算。

    场景：账号里有多家店，同一个 SPU 在别家店也有（或过滤参数没生效），
    不校验就会把别家店的商品当成模板复制，复制出来完全不相干。
    """
    client = FakeClient(
        template_row={
            "id": 111,
            "productName": "别家店的同名模板",
            "dxmState": "online",
            "shopId": 999999,          # ← 不是这家店
        },
    )
    outcome = make_pipeline(cfg_with_shop, row, client, live=True).run()

    assert outcome.failed
    step = by_name(outcome, "FIND_TEMPLATE")
    assert step.status is Status.FAIL
    assert "找不到" in step.detail
    assert "A hat tshirt" in step.detail
    assert not any("copy_product" in c for c in client.calls), "找不到模板就不能复制"


def test_find_template_searches_within_the_shop(cfg_with_shop, row):
    """搜索要带上核对出来的 shopId（不能全账号乱搜）。"""
    client = FakeClient(
        template_row={"id": 111, "productName": "模板商品", "dxmState": "online"}
    )
    outcome = make_pipeline(cfg_with_shop, row, client, live=True).run()

    assert by_name(outcome, "FIND_TEMPLATE").status is Status.OK
    assert any(
        f"shop_id={DEFAULT_SHOP_ID}" in c for c in client.calls
    ), f"没按店铺过滤：{client.calls[:5]}"


def test_existing_product_id_skips_copy_to_avoid_duplicate(cfg_with_shop, row):
    """表里已有商品ID → 绝不能再去复制一份（那就是重复铺货）。"""
    row.product_id = "184807703165779045"
    client = FakeClient(
        template_row={"id": 111, "productName": "模板商品", "dxmState": "online"},
        product=product_with_images(
            productName=row.title_cn,
            productNameI18n=json.dumps({"en": row.title_en}),
        ),
    )
    outcome = make_pipeline(cfg_with_shop, row, client, live=True, op=1).run()

    assert not outcome.failed, [s.line() for s in outcome.steps]
    by_name = {s.name: s for s in outcome.steps}
    assert by_name["COPY"].status is Status.SKIP
    assert by_name["FIND_NEW"].status is Status.SKIP
    assert outcome.product_id == "184807703165779045"
    assert not any("copy_product" in c for c in client.calls)
    # 已有商品的行是"重试/续跑"，不该重复往图片空间塞图
    assert by_name["UPLOAD"].status is Status.SKIP
    assert client.uploads == []
    # op=1（仅保存）且标题已一致、也没换图 → 不该白写一次
    assert by_name["APPLY"].status is Status.SKIP
    assert not any("save_product" in c for c in client.calls)


def test_swap_images_can_be_forced_on_an_existing_product(cfg_with_shop, row):
    """已有商品但图片不对时，可以强制换（swap_images=True）。"""
    row.product_id = "184807703165779045"
    client = FakeClient(
        template_row={"id": 111, "productName": "模板商品", "dxmState": "online"},
        product=product_with_images(
            productName=row.title_cn,
            productNameI18n=json.dumps({"en": row.title_en}),
        ),
    )
    outcome = make_pipeline(
        cfg_with_shop, row, client, live=True, op=1, swap_images=True
    ).run()

    assert not outcome.failed, [s.line() for s in outcome.steps]
    assert client.uploads, "强制换图就该真的传"
    assert not any("copy_product" in c for c in client.calls), "但仍然不能复制"


def test_publish_op_submits_even_when_title_unchanged(cfg_with_shop, row):
    """op=2/3 时那个 op 本身就是动作：标题没变也必须提交，否则永远发不出去。"""
    row.product_id = "184807703165779045"
    client = FakeClient(
        template_row={"id": 111, "productName": "模板商品", "dxmState": "online"},
        product=product_with_images(
            productName=row.title_cn,
            productNameI18n=json.dumps({"en": row.title_en}),
        ),
    )
    outcome = make_pipeline(cfg_with_shop, row, client, live=True, op=2).run()

    assert not outcome.failed, [s.line() for s in outcome.steps]
    assert not any("copy_product" in c for c in client.calls)
    assert client.saved_op == 2
    assert any("save_product(op=2)" in c for c in client.calls)
    assert "立即发布" in by_name(outcome, "APPLY").detail


def test_existing_product_id_still_republishes_when_title_differs(cfg_with_shop, row):
    """已有商品 + 标题不一致 → 在已有商品上改并保存（op=2 即重新发布）。"""
    row.product_id = "184807703165779045"
    client = FakeClient(
        template_row={"id": 111, "productName": "模板商品", "dxmState": "online"},
        product=product_with_images(id=184807703165779045),
    )
    outcome = make_pipeline(cfg_with_shop, row, client, live=True, op=2).run()

    assert not outcome.failed, [s.line() for s in outcome.steps]
    assert not any("copy_product" in c for c in client.calls)
    assert client.saved_product["productName"] == row.title_cn
    assert client.saved_op == 2


def test_existing_product_id_dry_run_never_saves(cfg_with_shop, row):
    """已有商品ID的干跑读的是那个真商品，但一步都不写。"""
    row.product_id = "184807703165779045"
    client = FakeClient(
        template_row={"id": 111, "productName": "模板商品", "dxmState": "online"},
        product={
            "productName": "旧标题",
            "productNameI18n": json.dumps({"en": "Old Title"}),
        },
    )
    outcome = make_pipeline(cfg_with_shop, row, client, live=False, op=2).run()

    assert not outcome.failed
    by_name = {s.name: s for s in outcome.steps}
    assert by_name["APPLY"].status is Status.DRY
    assert "表里已有的商品" in by_name["APPLY"].detail
    assert "旧标题" in by_name["APPLY"].detail
    assert not any("save_product" in c for c in client.calls)
    assert not any("copy_product" in c for c in client.calls)


def test_template_not_found_is_reported(cfg_with_shop, row):
    client = FakeClient(template_row=None)
    outcome = make_pipeline(cfg_with_shop, row, client).run()

    assert outcome.failed
    last = by_name(outcome, "FIND_TEMPLATE")
    assert last.status is Status.FAIL
    assert "8909735440" in last.detail


def test_dry_run_never_writes(cfg_with_shop, row):
    """干跑：只读步骤跑完，复制/保存一律不发，但要把"会改什么"提前算出来。"""
    client = FakeClient(
        template_row={"id": 111, "productName": "模板商品", "dxmState": "online"},
        product={
            "productName": "模板商品",
            "productNameI18n": json.dumps({"en": "Old T‐Shirt Title"}, ensure_ascii=False),
        },
    )
    outcome = make_pipeline(cfg_with_shop, row, client, live=False).run()

    assert not outcome.failed
    by_name = {s.name: s for s in outcome.steps}
    assert by_name["PRECHECK"].status is Status.OK
    assert by_name["SHOP_CHECK"].status is Status.OK
    assert by_name["FIND_TEMPLATE"].status is Status.OK
    assert by_name["COPY"].status is Status.DRY
    assert by_name["APPLY"].status is Status.DRY
    # 干跑也要让用户看见 diff（只读预览）
    assert row.title_cn in by_name["APPLY"].detail
    assert row.title_en in by_name["APPLY"].detail
    assert "product_edit(111)" in client.calls       # 只读模板，没有碰新商品
    assert not any("copy_product" in c for c in client.calls)
    assert not any("save_product" in c for c in client.calls)
    assert client.closed is True


def test_live_run_copies_and_saves_with_new_title(cfg_with_shop, row):
    client = FakeClient(
        template_row={"id": 111, "productName": "模板商品", "dxmState": "online"},
        product=product_with_images(
            productName="模板商品",
            productNameI18n=json.dumps({"en": "Old T‐Shirt Title"}, ensure_ascii=False),
            id=222,
        ),
        offline_rows=[{"id": 222, "productName": "模板商品", "dxmOfflineState": "waitPublish"}],
    )
    outcome = make_pipeline(cfg_with_shop, row, client, live=True, op=1).run()

    assert not outcome.failed, [s.line() for s in outcome.steps]
    assert any("copy_product(111,9013981)" in c for c in client.calls)
    assert client.saved_product is not None
    assert client.saved_product["productName"] == row.title_cn
    assert json.loads(client.saved_product["productNameI18n"])["en"] == row.title_en
    assert client.saved_op == 1
    assert client.closed is True


def test_dry_run_preview_degrades_when_template_unreadable(cfg_with_shop, row):
    """模板读不出商品对象时，干跑只是没法预览，不该判失败。"""
    client = FakeClient(
        template_row={"id": 111, "productName": "模板商品", "dxmState": "online"},
        product={},                                  # product_edit 返回空
    )
    outcome = make_pipeline(cfg_with_shop, row, client, live=False).run()

    assert not outcome.failed
    apply_step = by_name(outcome, "APPLY")
    assert apply_step.status is Status.DRY
    assert not any("save_product" in c for c in client.calls)


def test_live_run_uploads_and_swaps_the_images(cfg_with_shop, row):
    """真实跑必须把这一行的图换上去——否则就是「标题对、图片是模板的」错商品。"""
    client = FakeClient(
        template_row={"id": 111, "productName": "模板商品", "dxmState": "online"},
        product=product_with_images(),
        offline_rows=[{"id": 222, "productName": "模板商品"}],
    )
    outcome = make_pipeline(cfg_with_shop, row, client, live=True, op=1).run()

    assert not outcome.failed, [s.line() for s in outcome.steps]
    by_name = {s.name: s for s in outcome.steps}
    assert by_name["UPLOAD"].status is Status.OK

    # 上传了主图 + 素材图（传的是出好的合规图，文件名带 slot 前缀）
    assert len(client.uploads) == 2
    assert any(u == "main-00742093597.jpg" for u in client.uploads)
    assert any(u == "material-00742093597-素材图.jpg" for u in client.uploads)

    saved = client.saved_product
    previews = json.loads(saved["mainProductSkuSpecReqs"])[0]["previewImgUrls"]
    urls = previews.split("|")
    assert urls[0].endswith("cos.example.com/main-00742093597.jpg"), "新主图排第 1"
    assert previews.startswith("https://"), "店小秘要完整 https 地址"
    assert "https://old/1.jpg" not in previews, "模板的主图要被换掉"

    thumbs = {e["thumbUrl"] for e in json.loads(saved["variationListStr"])}
    assert all("cos.example.com/main-00742093597.jpg" in t for t in thumbs)

    # 素材图写的是顶层 materialImgUrl（不是素材图那一列的文件名推导出来的）
    assert "cos.example.com/material-00742093597-素材图.jpg" in saved["materialImgUrl"]
    assert saved["materialImgUrl"] != "https://old/material.jpg"


def test_dry_run_never_uploads(cfg_with_shop, row):
    """干跑一张图都不许传。"""
    client = FakeClient(
        template_row={"id": 111, "productName": "模板商品", "dxmState": "online"},
        product=product_with_images(),
    )
    outcome = make_pipeline(cfg_with_shop, row, client, live=False).run()

    assert not outcome.failed
    assert client.uploads == []
    by_name = {s.name: s for s in outcome.steps}
    assert by_name["UPLOAD"].status is Status.DRY


def test_the_kept_showcase_comes_from_the_template_not_the_product(cfg_with_shop, row):
    """要保留的展示图以**模板**为准。

    场景：这个商品之前被换错过（展示图已经丢了，轮播只剩 1 张）。如果从它自己身上
    取"第 2 张往后"，就什么都取不回来——那是修不好的。所以必须去读模板。
    """
    template_product = {
        "productName": "模板商品",
        "mainProductSkuSpecReqs": json.dumps(
            [{"previewImgUrls": "https://t/main.jpg|https://t/show1.jpg|https://t/show2.jpg"}],
            ensure_ascii=False,
        ),
        "variationListStr": json.dumps(
            [{"id": 1, "thumbUrl": "https://t/main.jpg"}], ensure_ascii=False
        ),
        "materialImgUrl": "https://t/material.jpg",
    }
    # 被编辑的商品：轮播只剩 1 张（展示图丢了）
    client = FakeClient(
        template_row={"id": 111, "productName": "模板商品", "dxmState": "online"},
        product=product_with_images(
            mainProductSkuSpecReqs=json.dumps(
                [{"previewImgUrls": "https://old/1.jpg"}], ensure_ascii=False
            )
        ),
        template_product=template_product,
        offline_rows=[{"id": 222, "productName": "模板商品"}],
    )
    outcome = make_pipeline(cfg_with_shop, row, client, live=True, op=1).run()

    assert not outcome.failed, [s.line() for s in outcome.steps]
    urls = json.loads(client.saved_product["mainProductSkuSpecReqs"])[0]["previewImgUrls"].split("|")
    assert urls[0].endswith("main-00742093597.jpg"), "新主图第 1"
    assert urls[1:] == ["https://t/show1.jpg", "https://t/show2.jpg"], (
        "展示图要从模板取回来，不是从这个商品自己身上取"
    )


def test_colors_survive_the_whole_pipeline(cfg_with_shop, row):
    """整条流程跑完，商品的颜色/规格必须一模一样（用户明确要求不动颜色）。"""
    p = product_with_images()
    spec_before = json.loads(p["mainProductSkuSpecReqs"])[0]
    sku_before = json.loads(p["variationListStr"])

    client = FakeClient(
        template_row={"id": 111, "productName": "模板商品", "dxmState": "online"},
        product=p,
        offline_rows=[{"id": 222, "productName": "模板商品"}],
    )
    outcome = make_pipeline(cfg_with_shop, row, client, live=True, op=1).run()
    assert not outcome.failed, [s.line() for s in outcome.steps]

    saved = client.saved_product
    spec_after = json.loads(saved["mainProductSkuSpecReqs"])[0]
    for key in spec_before:
        if key != "previewImgUrls":
            assert spec_after[key] == spec_before[key], f"{key} 被改动了"

    sku_after = json.loads(saved["variationListStr"])
    assert len(sku_after) == len(sku_before), "规格条数不能变"
    for before, after in zip(sku_before, sku_after):
        for key in before:
            if key != "thumbUrl":
                assert after[key] == before[key], f"{key} 被改动了"


# ------------------------------------------------------ 认"刚复制出来的新商品"
def test_find_new_picks_the_product_that_was_not_there_before(cfg_with_shop, row):
    """认新商品靠"复制后多出来的那一个"，不是靠标题。"""
    client = FakeClient(
        template_row={"id": 111, "productName": "模板商品", "dxmState": "online"},
        product=product_with_images(),
        offline_rows=[{"id": 900, "productName": "别家的商品"}],
        copy_adds={"id": 222, "productName": "模板商品"},
    )
    outcome = make_pipeline(cfg_with_shop, row, client, live=True, op=1).run()

    assert not outcome.failed, [s.line() for s in outcome.steps]
    assert outcome.product_id == "222"
    assert "多出来的那一个" in by_name(outcome, "FIND_NEW").detail


def test_find_new_ignores_a_stale_copy_with_the_template_name(cfg_with_shop, row):
    """上一行复制成功但改名失败留下的"僵尸"商品，不能被这一行捡走。

    场景：复制前待发布里**已经**有一个标题还等于模板名的商品（id=555）。
    老写法一看到它就认，会把这一行的图和标题写到那个僵尸商品身上。
    """
    client = FakeClient(
        template_row={"id": 111, "productName": "模板商品", "dxmState": "online"},
        product=product_with_images(),
        offline_rows=[{"id": 555, "productName": "模板商品"}],   # ← 僵尸
        copy_adds={"id": 222, "productName": "模板商品"},
    )
    outcome = make_pipeline(cfg_with_shop, row, client, live=True, op=1).run()

    assert not outcome.failed, [s.line() for s in outcome.steps]
    assert outcome.product_id == "222", "必须认新复制出来的 222，不是僵尸 555"


def test_find_new_aborts_when_multiple_products_appeared(cfg_with_shop, row):
    """复制后多出好几个 → 认不准，宁可中止，别铺错货。"""
    client = FakeClient(
        template_row={"id": 111, "productName": "模板商品", "dxmState": "online"},
        product=product_with_images(),
        offline_rows=[{"id": 900, "productName": "别家的商品"}],
    )
    client.copy_adds = None

    def copy_then_add_two(product_id, shop_ids):
        client.calls.append("copy_product")
        client.offline_rows = list(client.offline_rows) + [
            {"id": 222, "productName": "模板商品"},
            {"id": 333, "productName": "模板商品"},
        ]
        return {"code": 0, "msg": "ok"}

    client.copy_product = copy_then_add_two
    outcome = make_pipeline(cfg_with_shop, row, client, live=True, op=1).run()

    assert outcome.failed
    assert "认不准" in by_name(outcome, "FIND_NEW").detail
    assert not any("save_product" in c for c in client.calls), "没认准就绝不能保存"


def test_apply_aborts_when_the_product_has_no_image_fields(cfg_with_shop, row):
    """上传成功了但商品里没有可写的图片字段 → 必须中止，不能存成错商品。"""
    client = FakeClient(
        template_row={"id": 111, "productName": "模板商品", "dxmState": "online"},
        product={"productName": "旧标题", "productNameI18n": "{}"},   # 没有任何图片字段
        offline_rows=[{"id": 222, "productName": "模板商品"}],
    )
    outcome = make_pipeline(cfg_with_shop, row, client, live=True, op=1).run()

    assert outcome.failed
    apply_step = by_name(outcome, "APPLY")
    assert apply_step.status is Status.FAIL
    assert "图片" in apply_step.detail
    assert not any("save_product" in c for c in client.calls), "宁可不动，也不能存错"


def test_live_run_reports_copy_failure(cfg_with_shop, row):
    client = FakeClient(
        template_row={"id": 111, "productName": "模板商品", "dxmState": "online"},
        copy_code=-1,
    )
    outcome = make_pipeline(cfg_with_shop, row, client, live=True).run()

    assert outcome.failed
    copy_step = by_name(outcome, "COPY")
    assert copy_step.status is Status.FAIL
    assert "米豆不足" in copy_step.detail
    assert not any("save_product" in c for c in client.calls)


def test_live_run_reports_missing_new_product(cfg_with_shop, row):
    """原工具专门处理过的坑：复制成功但待发布列表里没找到新商品。"""
    client = FakeClient(
        template_row={"id": 111, "productName": "模板商品", "dxmState": "online"},
        offline_rows=[{"id": 999, "productName": "别的商品"}],
    )
    outcome = make_pipeline(cfg_with_shop, row, client, live=True).run()

    assert outcome.failed
    find_step = by_name(outcome, "FIND_NEW")
    assert find_step.status is Status.FAIL
    assert "没找到新商品" in find_step.detail
