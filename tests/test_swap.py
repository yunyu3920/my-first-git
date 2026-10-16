"""换图补丁（`imaging/swap.py`）的测试。

这里最容易错的地方：`mainProductSkuSpecReqs` / `variationListStr` 在接口里是
**JSON 字符串**，不是对象。改错了会静默失败——保存下去还是模板的图。

另一条规矩（2026-10-16 用户定）：**只换主图，展示图保留**，
而且**颜色/规格一律不碰**。
"""

from __future__ import annotations

import json

from temu_bot.imaging.swap import (
    ImagePatch,
    describe_product_images,
    patch_product_images,
    preview_urls,
    showcase_images,
)

MAIN = ["https://cos.example.com/main-1.jpg", "https://cos.example.com/main-2.jpg"]
MATERIAL = "https://cos.example.com/material.jpg"

# 模板的轮播：第 1 张是主图，后面 2 张是展示图
TEMPLATE_PREVIEWS = "https://old/main.jpg|https://old/show1.jpg|https://old/show2.jpg"
TEMPLATE_SHOWCASE = ["https://old/show1.jpg", "https://old/show2.jpg"]


def product(**overrides):
    """一个"接口返回原样"的商品：那两个图片字段是 JSON 字符串。"""
    base = {
        "productName": "旧标题",
        "mainProductSkuSpecReqs": json.dumps(
            [
                {
                    "specId": 15071,
                    "parentSpecId": 1001,
                    "parentSpecName": "颜色",
                    "specName": "深灰色",
                    "previewImgUrls": TEMPLATE_PREVIEWS,
                }
            ],
            ensure_ascii=False,
        ),
        "variationListStr": json.dumps(
            [
                {
                    "id": 1,
                    "thumbUrl": "https://old/main.jpg",
                    "productSkuSpecReqs": (
                        '[{"specId":15071,"parentSpecName":"颜色","specName":"深灰色"},'
                        '{"specId":10004,"parentSpecName":"尺码","specName":"S"}]'
                    ),
                },
                {
                    "id": 2,
                    "thumbUrl": "https://old/main.jpg",
                    "productSkuSpecReqs": (
                        '[{"specId":15071,"parentSpecName":"颜色","specName":"深灰色"},'
                        '{"specId":9005,"parentSpecName":"尺码","specName":"M"}]'
                    ),
                },
            ],
            ensure_ascii=False,
        ),
        "materialImgUrl": "https://old/material.jpg",
    }
    base.update(overrides)
    return base


def carousel(p):
    return preview_urls(p)


# ------------------------------------------------------- 轮播：只换主图、留展示图
def test_new_main_goes_first_and_template_showcase_is_kept():
    """规矩：删掉模板主图 → 新主图顶到第 1 位 → 模板的展示图原样保留。"""
    p = product()
    patch = patch_product_images(
        p, main_urls=MAIN, material_url=MATERIAL, keep_previews=TEMPLATE_SHOWCASE
    )

    assert patch, "改到了东西，必须为真"
    assert not patch.warnings
    assert carousel(p) == MAIN + TEMPLATE_SHOWCASE
    assert "https://old/main.jpg" not in carousel(p), "模板的主图要被删掉"


def test_showcase_is_kept_when_not_told_otherwise():
    """不传 keep_previews → 用这个商品自己现有的展示图（第 2 张往后）。"""
    p = product()
    patch_product_images(p, main_urls=MAIN, material_url=MATERIAL)

    assert carousel(p) == MAIN + TEMPLATE_SHOWCASE


def test_single_image_template_has_nothing_to_keep():
    p = product(
        mainProductSkuSpecReqs=json.dumps(
            [{"previewImgUrls": "https://old/main.jpg"}], ensure_ascii=False
        )
    )
    patch_product_images(p, main_urls=MAIN, material_url=MATERIAL)

    assert carousel(p) == MAIN


def test_showcase_helper_takes_everything_after_the_first():
    p = product()
    assert showcase_images(p) == TEMPLATE_SHOWCASE


def test_repeated_swap_is_idempotent():
    """换两遍结果一样——重试/续跑时不会越换越多。"""
    p = product()
    patch_product_images(p, main_urls=MAIN, keep_previews=TEMPLATE_SHOWCASE)
    once = carousel(p)
    patch_product_images(p, main_urls=MAIN, keep_previews=TEMPLATE_SHOWCASE)

    assert carousel(p) == once
    assert len(carousel(p)) == len(MAIN) + len(TEMPLATE_SHOWCASE)


def test_thumb_becomes_the_new_main():
    p = product()
    patch_product_images(
        p, main_urls=MAIN, material_url=MATERIAL, keep_previews=TEMPLATE_SHOWCASE
    )

    for entry in json.loads(p["variationListStr"]):
        assert entry["thumbUrl"] == MAIN[0], "缩略图用新主图（第 1 张）"

    assert carousel(p) == MAIN + TEMPLATE_SHOWCASE
    assert p["materialImgUrl"] == MATERIAL


# ------------------------------------------------------- 颜色/规格一个字都不许动
def test_colors_and_specs_are_left_untouched():
    """用户明确要求：颜色选项按模板不动。换图只许改图片地址。"""
    p = product()
    before_skc = json.loads(p["mainProductSkuSpecReqs"])[0]
    before_reqs = [e["productSkuSpecReqs"] for e in json.loads(p["variationListStr"])]

    patch_product_images(
        p, main_urls=MAIN, material_url=MATERIAL, keep_previews=TEMPLATE_SHOWCASE
    )

    after_skc = json.loads(p["mainProductSkuSpecReqs"])[0]
    for key in ("specId", "parentSpecId", "parentSpecName", "specName"):
        assert after_skc[key] == before_skc[key], f"{key} 被改动了"

    after_reqs = [e["productSkuSpecReqs"] for e in json.loads(p["variationListStr"])]
    assert after_reqs == before_reqs, "SKU 的颜色/尺码规格被改动了"


def test_all_other_keys_survive_the_round_trip():
    """重新序列化不能把别的键弄丢（id/尺码/宽度…）。"""
    p = product(
        variationListStr=json.dumps(
            [{"id": 7, "thumbUrl": "https://old/main.jpg", "width": 250, "widthDouble": 25.0}],
            ensure_ascii=False,
        )
    )
    patch_product_images(p, main_urls=MAIN, keep_previews=TEMPLATE_SHOWCASE)

    entry = json.loads(p["variationListStr"])[0]
    assert entry["id"] == 7
    assert entry["width"] == 250
    assert entry["widthDouble"] == 25.0
    assert entry["thumbUrl"] == MAIN[0]


# ------------------------------------------------------------------ 镜像字段
def test_mirror_fields_are_updated_too():
    """mainProductSkuSpecReqsList / variations 是镜像字段，也要一起换。"""
    p = product(
        mainProductSkuSpecReqsList=json.dumps(
            [{"previewImgUrls": TEMPLATE_PREVIEWS}], ensure_ascii=False
        ),
        variations=json.dumps([{"thumbUrl": "https://old/main.jpg"}], ensure_ascii=False),
    )
    patch = patch_product_images(
        p, main_urls=MAIN, material_url=MATERIAL, keep_previews=TEMPLATE_SHOWCASE
    )

    expected = "|".join(MAIN + TEMPLATE_SHOWCASE)
    assert json.loads(p["mainProductSkuSpecReqsList"])[0]["previewImgUrls"] == expected
    assert json.loads(p["variations"])[0]["thumbUrl"] == MAIN[0]
    assert any("mainProductSkuSpecReqsList" in n for n in patch.notes)
    assert any("variations" in n for n in patch.notes)


def test_list_typed_fields_are_handled_as_well():
    """万一某个商品返回的是真列表（不是 JSON 字符串），也不能炸。"""
    p = product(
        mainProductSkuSpecReqs=[{"previewImgUrls": "https://old/main.jpg"}],
        variationListStr=[{"thumbUrl": "https://old/main.jpg"}],
    )
    patch_product_images(p, main_urls=MAIN, material_url=MATERIAL)

    assert isinstance(p["mainProductSkuSpecReqs"], list)
    assert p["mainProductSkuSpecReqs"][0]["previewImgUrls"] == "|".join(MAIN)
    assert p["variationListStr"][0]["thumbUrl"] == MAIN[0]


# ------------------------------------------------------------------ 失败与告警
def test_no_writable_field_is_falsy():
    """商品里一个图片字段都没有 → 结果必须为假，上层据此中止。"""
    p = {"productName": "旧标题"}
    patch = patch_product_images(p, main_urls=MAIN, material_url=MATERIAL)

    assert not patch
    assert patch.notes == []


def test_missing_material_field_is_a_warning_not_a_failure():
    """轮播换上了、但没有 materialImgUrl → 算成功，同时必须留下告警。"""
    p = product(materialImgUrl=None)
    del p["materialImgUrl"]
    patch = patch_product_images(p, main_urls=MAIN, material_url=MATERIAL)

    assert patch, "轮播换上了就算成功"
    assert patch.notes
    assert patch.warnings and "materialImgUrl" in patch.warnings[0]
    assert "materialImgUrl" not in p, "不能凭空塞一个字段进去"


def test_material_only_when_product_has_the_field():
    """只有素材图要换、商品也正好有那个字段 → 只改它。"""
    p = product()
    patch = patch_product_images(p, main_urls=[], material_url=MATERIAL)

    assert patch.notes == ["materialImgUrl: 素材图"]
    assert p["materialImgUrl"] == MATERIAL
    assert carousel(p) == TEMPLATE_PREVIEWS.split("|"), "没给主图就别动轮播"


def test_nothing_to_do():
    p = product()
    before = json.dumps(p, ensure_ascii=False, sort_keys=True)
    patch = patch_product_images(p, main_urls=[], material_url=None)

    assert patch == ImagePatch()
    assert json.dumps(p, ensure_ascii=False, sort_keys=True) == before


def test_blank_urls_are_dropped():
    """空字符串（上传没拿到地址）不能被拼进竖线串里。"""
    p = product()
    patch_product_images(p, main_urls=["", MAIN[0], ""], material_url=MATERIAL)
    assert carousel(p) == [MAIN[0]] + TEMPLATE_SHOWCASE

    # 主图全是空的 → 等于没给主图，轮播就不该动
    p2 = product()
    patch_product_images(p2, main_urls=["", ""], material_url=MATERIAL)
    assert carousel(p2) == TEMPLATE_PREVIEWS.split("|")


def test_blank_showcase_entries_are_dropped():
    """模板的展示图列表里混进空串也不能拼出 "a||b"。"""
    p = product()
    patch_product_images(p, main_urls=MAIN, keep_previews=["https://old/show1.jpg", "", "  "])

    assert carousel(p) == MAIN + ["https://old/show1.jpg"]


# ------------------------------------------------------------------ 干跑用
def test_describe_shows_what_is_currently_on_the_product():
    info = describe_product_images(product())

    assert info["preview_count"] == 3
    assert info["preview_first"] == "https://old/main.jpg"
    assert info["showcase_count"] == 2, "3 张轮播里第 1 张是主图，剩 2 张展示图"
    assert info["thumb_count"] == 1, "两个 SKU 共用一个缩略图"
    assert info["material"] == "https://old/material.jpg"


def test_describe_on_an_empty_product_does_not_raise():
    info = describe_product_images({})

    assert info == {
        "preview_count": 0,
        "preview_first": "",
        "showcase_count": 0,
        "thumb_count": 0,
        "material": "",
    }
