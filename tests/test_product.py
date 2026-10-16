"""英文标题归一化测试——用真实失败样本钉住行为。"""

from __future__ import annotations

import json

from temu_bot.product import (
    bad_chars,
    fix_en_title,
    get_en_title,
    has_non_ascii,
    normalize_en_title,
    set_en_title,
    set_sku_code,
    sku_codes,
)

# 真实失败样本（2026-10-15，店小秘 id 180094165257309530）
REAL_BAD = (
    "Women Long Sleeve T‐Shirt, Christmas Creative Drawing Print, "
    "Stretch Fabric, Holiday Gift Basic Top For Commute Daily Wear"
)


def test_real_failure_sample_is_fixed():
    """失败原因就是 U+2010 冒充 '-'；归一化后必须变成纯 ASCII 且语义不变。"""
    assert has_non_ascii(REAL_BAD)
    assert bad_chars(REAL_BAD) == ["‐"]

    fixed = normalize_en_title(REAL_BAD)
    assert not has_non_ascii(fixed)
    assert "T-Shirt" in fixed
    assert fixed.startswith("Women Long Sleeve T-Shirt, Christmas")
    # 除那一个字符外，其余内容必须逐字保留
    assert fixed == REAL_BAD.replace("‐", "-")


def test_good_title_is_untouched():
    good = "Women's Long Sleeve T-Shirt French Fries Emoji Print Crew Neck Fall Winter Sporty Casual"
    assert normalize_en_title(good) == good
    assert not has_non_ascii(good)


def test_common_lookalikes_normalized():
    assert normalize_en_title("Men’s T‑Shirt – Blue") == "Men's T-Shirt - Blue"
    assert normalize_en_title("Women“ s Tee”") == 'Women" s Tee"'
    assert normalize_en_title("A B") == "A B"           # 不间断空格
    assert normalize_en_title("Cool… Tee") == "Cool... Tee"
    # 全角逗号按 1:1 换成半角（不自动补空格：可预测优先，避免"聪明"的改写）
    assert normalize_en_title("Red，Blue") == "Red,Blue"
    assert normalize_en_title("Size（L）") == "Size(L)"
    assert normalize_en_title("Zero​Width") == "ZeroWidth"


def test_cjk_and_emoji_are_stripped_not_translated():
    """汉字/表情会被清掉；残留的单个 ASCII 字母保留，是否可用交给上层判断。"""
    assert normalize_en_title("女士长袖T恤") == "T"          # 只剩裸的 T，上层应视为不可用
    assert normalize_en_title("Tee 🎄 Christmas") == "Tee Christmas"
    assert normalize_en_title("T恤 Shirt") == "T Shirt"


def test_whitespace_is_collapsed():
    assert normalize_en_title("  A   B \t C ") == "A B C"
    assert normalize_en_title("") == ""
    assert normalize_en_title("...") == ""


def test_product_en_title_roundtrip():
    product = {"productNameI18n": json.dumps({"en": REAL_BAD}, ensure_ascii=False)}

    assert get_en_title(product) == REAL_BAD
    original, fixed = fix_en_title(product)
    assert original == REAL_BAD
    assert "T-Shirt" in fixed

    # 写回后仍保持 JSON 字符串形态（前端就是这个格式）
    stored = product["productNameI18n"]
    assert isinstance(stored, str)
    assert json.loads(stored)["en"] == fixed


def test_set_en_title_keeps_other_languages():
    product = {"productNameI18n": json.dumps({"en": "old", "fr": "vieux"}, ensure_ascii=False)}
    set_en_title(product, "new")
    mapping = json.loads(product["productNameI18n"])
    assert mapping["en"] == "new"
    assert mapping["fr"] == "vieux"        # 别的语种不能被弄丢


def test_get_en_title_tolerates_odd_shapes():
    assert get_en_title({}) == ""
    assert get_en_title({"productNameI18n": None}) == ""
    assert get_en_title({"productNameI18n": {"en": "x"}}) == "x"
    assert get_en_title({"productNameI18n": "not json"}) == ""


# ------------------------------------------------------------------ SKU 货号
# 实测（对着老工具上架成功的 98 个商品核过）：
#   SKU 级  variationListStr[*].extCode = 表里第 9 列那个前缀编码
#   SKC 级  mainProductSkuSpecReqs[*].extCode = 模板自己的，别动
SKU_CODE = "00742093597"
TEMPLATE_SKC_CODE = "ZD001"


def product_with_codes(**overrides):
    """一个"接口返回原样"的商品：两个列表字段都是 JSON 字符串。"""
    base = {
        "mainProductSkuSpecReqs": json.dumps(
            [
                {
                    "parentSpecName": "颜色",
                    "specName": "深灰色",
                    "extCode": TEMPLATE_SKC_CODE,          # SKC 级：模板的
                    "previewImgUrls": "a.jpg|b.jpg",
                }
            ],
            ensure_ascii=False,
        ),
        "variationListStr": json.dumps(
            [
                {"id": 1, "extCode": "99689509419", "thumbUrl": "a.jpg"},
                {"id": 2, "extCode": "99689509419", "thumbUrl": "a.jpg"},
                {"id": 3, "extCode": "99689509419", "thumbUrl": "a.jpg"},
            ],
            ensure_ascii=False,
        ),
    }
    base.update(overrides)
    return base


def test_set_sku_code_fills_every_sku():
    p = product_with_codes()
    changed, before = set_sku_code(p, sku_code=SKU_CODE)

    assert changed == 3, "3 个 SKU 都该换"
    assert before == "99689509419"
    for entry in json.loads(p["variationListStr"]):
        assert entry["extCode"] == SKU_CODE


def test_set_sku_code_leaves_the_skc_level_code_alone():
    """SKC 级的 extCode 是模板自己的，老工具也没动它——不能顺手改了。"""
    p = product_with_codes()
    set_sku_code(p, sku_code=SKU_CODE)

    skc = json.loads(p["mainProductSkuSpecReqs"])[0]
    assert skc["extCode"] == TEMPLATE_SKC_CODE
    assert skc["specName"] == "深灰色"


def test_set_sku_code_is_a_noop_when_already_right():
    p = product_with_codes(
        variationListStr=json.dumps([{"id": 1, "extCode": SKU_CODE}], ensure_ascii=False)
    )
    changed, before = set_sku_code(p, sku_code=SKU_CODE)

    assert changed == 0
    assert before == SKU_CODE


def test_set_sku_code_ignores_an_empty_code():
    p = product_with_codes()
    before_json = json.dumps(p, ensure_ascii=False, sort_keys=True)
    changed, _ = set_sku_code(p, sku_code="")

    assert changed == 0
    assert json.dumps(p, ensure_ascii=False, sort_keys=True) == before_json


def test_set_sku_code_handles_the_list_form_too():
    """`variations` 在接口里是真列表；万一商品返回的是它，也不能炸。"""
    p = product_with_codes(
        variationListStr=[{"id": 1, "extCode": "99689509419"}],
        variations=[{"id": 1, "extCode": "99689509419"}],
    )
    set_sku_code(p, sku_code=SKU_CODE)

    assert isinstance(p["variationListStr"], list)
    assert p["variationListStr"][0]["extCode"] == SKU_CODE
    assert p["variations"][0]["extCode"] == SKU_CODE


def test_set_sku_code_reports_mixed_old_codes():
    """商品上本来挂了好几种货号（状态不干净）→ before 要都列出来。"""
    p = product_with_codes(
        variationListStr=json.dumps(
            [{"id": 1, "extCode": "A"}, {"id": 2, "extCode": "B"}], ensure_ascii=False
        )
    )
    _, before = set_sku_code(p, sku_code=SKU_CODE)

    assert before == "A、B"


def test_sku_codes_dedupes_and_keeps_order():
    p = product_with_codes(
        variationListStr=json.dumps(
            [{"id": 1, "extCode": "B"}, {"id": 2, "extCode": "A"}, {"id": 3, "extCode": "B"}],
            ensure_ascii=False,
        )
    )
    assert sku_codes(p) == ["B", "A"]


def test_sku_codes_on_an_empty_product():
    assert sku_codes({}) == []
