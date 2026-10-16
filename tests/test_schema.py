from __future__ import annotations

from temu_bot.sheet.schema import (
    COL_FOLDER,
    COL_MAIN_IMAGE,
    COL_TEMPLATE_SPU,
    EXPECTED_HEADER,
    build_row,
    header_mismatches,
    make_row_key,
    normalize_cell,
    split_multi,
)


def test_normalize_cell_handles_numbers_and_none():
    assert normalize_cell(None) == ""
    assert normalize_cell("  x  ") == "x"
    assert normalize_cell(1.0) == "1"          # openpyxl 常把整数读成 float
    assert normalize_cell(8909735440.0) == "8909735440"
    assert normalize_cell(1.5) == "1.5"


def test_row_key_is_stable_and_row_number_independent():
    a = make_row_key("C:/tmp/a", "1.jpg", "8909735440")
    b = make_row_key("C:/tmp/a", "1.jpg", "8909735440")
    c = make_row_key("C:/tmp/a", "2.jpg", "8909735440")
    assert a == b
    assert a != c
    assert len(a) == 16


def test_split_multi_single_value_and_fallback():
    # 现有真实数据：每格只有一个文件名
    assert split_multi("00742093597.jpg", "\n", r"[\n\r,，;；|]+") == ["00742093597.jpg"]
    # 换行分隔（配置的分隔符）
    assert split_multi("a.jpg\nb.jpg", "\n", "") == ["a.jpg", "b.jpg"]
    # 配置分隔符不在，但出现逗号 → 兜底正则生效
    assert split_multi("a.jpg,b.jpg", "\n", r"[\n\r,，;；|]+") == ["a.jpg", "b.jpg"]
    assert split_multi("", "\n", "") == []


def test_header_mismatches_detects_drift():
    good = dict(EXPECTED_HEADER)
    assert header_mismatches(good) == []

    drifted = dict(EXPECTED_HEADER)
    drifted[COL_TEMPLATE_SPU] = "模板SPU"          # 改名
    problems = header_mismatches(drifted)
    assert len(problems) == 1
    assert "模板产品SPUID" in problems[0]


def test_build_row_requires_folder_and_main_image():
    cells = {
        COL_FOLDER: "C:/tmp/a",
        COL_MAIN_IMAGE: "1.jpg",
        COL_TEMPLATE_SPU: "8909735440",
    }
    row = build_row(2, cells, separator="\n", fallback_pattern="")
    assert row is not None
    assert row.main_image == "1.jpg"
    assert row.images["main"] == ["1.jpg"]

    assert build_row(3, {COL_FOLDER: "C:/tmp/a"}, separator="\n", fallback_pattern="") is None
    assert build_row(4, {COL_MAIN_IMAGE: "1.jpg"}, separator="\n", fallback_pattern="") is None


def test_build_row_main_image_index_parsing():
    base = {COL_FOLDER: "C:/tmp/a", COL_MAIN_IMAGE: "1.jpg", COL_TEMPLATE_SPU: "1"}
    from temu_bot.sheet.schema import COL_MAIN_IMAGE_INDEX

    assert build_row(2, {**base, COL_MAIN_IMAGE_INDEX: "1"}, separator="\n", fallback_pattern="").main_image_index == 1
    assert build_row(3, {**base, COL_MAIN_IMAGE_INDEX: ""}, separator="\n", fallback_pattern="").main_image_index is None
    assert build_row(4, {**base, COL_MAIN_IMAGE_INDEX: "x"}, separator="\n", fallback_pattern="").main_image_index is None
