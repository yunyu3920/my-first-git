"""发布失败分类测试（分类规则是纯逻辑，必须钉住）。"""

from __future__ import annotations

from temu_bot.dianxiaomi.failures import Fix, build_triage, classify


def test_daily_quota_is_wait_not_autofix():
    a = classify("Temu对不同店铺有每天发品的数量限制，您在Temu后台发品一样会提示，请联系Temu买手/运营询问是否可提升上限")
    assert a.code == "DAILY_QUOTA"
    assert a.fix is Fix.WAIT_QUOTA

    b = classify("接口报错:创建商品数量超过当日额度，请于次日创建。")
    assert b.code == "DAILY_QUOTA"


def test_english_title_bad_chars_is_autofixable():
    category = classify("接口报错:英文名称不可输入非英文字母或表情符号")
    assert category.code == "EN_TITLE_BAD_CHARS"
    assert category.fix is Fix.AUTO_FIX_TITLE


def test_banned_words_is_autofixable_via_rewrite():
    category = classify("接口报错:Chinese or English title contains non-compliant words")
    assert category.code == "TITLE_BANNED_WORDS"
    assert category.fix is Fix.AUTO_FIX_TITLE


def test_auth_expired_needs_user_reauthorization():
    """实测：整店发不出去，Temu 侧授权过期，工具救不了。"""
    category = classify(
        "创建尺码表失败：接口报错：access_token is expired, please contact seller to "
        "reauthorize and share the new access_token with you."
    )
    assert category.code == "AUTH_EXPIRED"
    assert category.fix is Fix.REAUTH


def test_image_spec_is_autofixable_by_image_pipeline():
    """实测：主图 1340x1784，比要求的 1785 矮 1 像素 → 发布被拒。"""
    category = classify(
        "接口报错:服饰类目skc轮播图[https://x/y.jpeg]校验失败，应符合宽高比例为3:4，"
        "宽>=1340px，高>=1785px，<=2M"
    )
    assert category.code == "IMAGE_SPEC"
    assert category.fix is Fix.AUTO_FIX_IMAGE


def test_auth_expired_is_not_misread_as_transient():
    """授权过期 vs 临时异常：前者重试多少次都没用，不能混为一谈。"""
    assert classify("接口报错：access_token is expired") is not classify("接口报错:系统异常")


def test_transient_is_retryable():
    category = classify("接口报错:系统异常")
    assert category.code == "TRANSIENT"
    assert category.fix is Fix.AUTO_RETRY


def test_unknown_and_empty():
    assert classify("").code == "UNKNOWN"
    assert classify(None).code == "UNKNOWN"
    assert classify("某种没见过的新错误").code == "UNKNOWN"
    assert classify("某种没见过的新错误").fix is Fix.MANUAL


def test_build_triage_counts_like_real_data():
    """照着 2026-10-15 的真实分布造数据，核对汇总口径。"""
    rows = (
        [{"id": str(i), "productName": f"配额商品{i}", "errMsg": "Temu对不同店铺有每天发品的数量限制", "dxmOfflineState": "publishFail"} for i in range(203)]
        + [{"id": str(i), "productName": f"英文{i}", "errMsg": "接口报错:英文名称不可输入非英文字母或表情符号", "dxmOfflineState": "publishFail"} for i in range(195)]
        + [{"id": str(i), "productName": f"额度{i}", "errMsg": "接口报错:创建商品数量超过当日额度，请于次日创建。", "dxmOfflineState": "publishFail"} for i in range(4)]
        + [{"id": str(i), "productName": f"异常{i}", "errMsg": "接口报错:系统异常", "dxmOfflineState": "publishFail"} for i in range(3)]
        + [{"id": str(i), "productName": f"违规{i}", "errMsg": "接口报错:Chinese or English title contains non-compliant words", "dxmOfflineState": "publishFail"} for i in range(3)]
    )
    triage = build_triage(rows)

    assert len(triage.failures) == 408
    counts = triage.counts()
    assert counts["DAILY_QUOTA"] == 207          # 203 + 4
    assert counts["EN_TITLE_BAD_CHARS"] == 195
    assert counts["TRANSIENT"] == 3
    assert counts["TITLE_BANNED_WORDS"] == 3

    by_fix = triage.counts_by_fix()
    assert by_fix[Fix.WAIT_QUOTA.value] == 207
    assert by_fix[Fix.AUTO_FIX_TITLE.value] == 198   # 195 + 3，工具能自己改标题救回来
    assert by_fix[Fix.AUTO_RETRY.value] == 3


def test_build_triage_skips_healthy_rows():
    rows = [{"id": "1", "productName": "正常商品", "errMsg": None, "dxmOfflineState": "waitPublish"}]
    assert build_triage(rows).failures == []
