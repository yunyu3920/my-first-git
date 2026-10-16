"""收集图片测试：配对规则、缺素材图跳过、绝不覆盖已有表。"""

from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest
from PIL import Image

from temu_bot.collect import (
    CollectOptions,
    default_output,
    safe_output_path,
    scan_folder,
    write_table,
)
from temu_bot.sheet import schema

MAIN_SIZE = (1340, 1785)
MAT_SIZE = (800, 800)


def img(path: Path, size=MAIN_SIZE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (30, 90, 160)).save(path, quality=85)
    return path


@pytest.fixture()
def src(tmp_path: Path) -> Path:
    """一个典型素材目录：3 对完整的 + 1 张缺素材图 + 1 张多余的素材图。"""
    root = tmp_path / "素材"
    root.mkdir()
    img(root / "00742093597.jpg")
    img(root / "00742093597-素材图.jpg", MAT_SIZE)
    img(root / "01409714133.jpg")
    img(root / "01409714133-素材图.jpg", MAT_SIZE)
    img(root / "01564109050.jpg")
    img(root / "01564109050-素材图.jpg", MAT_SIZE)
    img(root / "09999999999.jpg")            # ← 没有素材图，应被跳过
    img(root / "05000000000-素材图.jpg", MAT_SIZE)   # ← 没有主图，不该被当成主图
    (root / "说明.txt").write_text("不是图片", encoding="utf-8")
    return root


def opts(folder: Path, **kw) -> CollectOptions:
    base = {"folder": folder, "shop": "A lovely costume", "template_spu": "8909735440"}
    base.update(kw)
    return CollectOptions(**base)


# ------------------------------------------------------------------ 配对
def test_pairs_main_with_material(src: Path):
    result = scan_folder(opts(src))

    assert result.image_count == 8          # 图片数（不含 txt）
    assert [r.main_name for r in result.rows] == [
        "00742093597.jpg",
        "01409714133.jpg",
        "01564109050.jpg",
    ]
    assert result.rows[0].material_name == "00742093597-素材图.jpg"
    assert result.rows[0].stem == "00742093597"


def test_row_without_material_is_skipped(src: Path):
    """用户明确要求：缺素材图就跳过该行。"""
    result = scan_folder(opts(src))

    assert "09999999999.jpg" not in [r.main_name for r in result.rows]
    skips = {s.name: s.reason for s in result.skipped}
    assert "09999999999.jpg" in skips
    assert "素材图" in skips["09999999999.jpg"]


def test_material_only_file_is_not_treated_as_main(src: Path):
    """只有素材图、没有主图的，不能当成一行商品。"""
    result = scan_folder(opts(src))
    assert "05000000000-素材图.jpg" not in [r.main_name for r in result.rows]


def test_rows_are_sorted_by_name(src: Path):
    names = [r.main_name for r in scan_folder(opts(src)).rows]
    assert names == sorted(names, key=str.lower)


def test_missing_folder_is_reported(tmp_path: Path):
    result = scan_folder(opts(tmp_path / "没有这个目录"))
    assert not result.ok
    assert "不存在" in result.skipped[0].reason


def test_empty_folder_is_reported(tmp_path: Path):
    empty = tmp_path / "空的"
    empty.mkdir()
    result = scan_folder(opts(empty))
    assert not result.ok
    assert "一张图都没有" in result.skipped[0].reason


def test_extension_mismatch_still_pairs(tmp_path: Path):
    """主图 .jpg、素材图 .png —— 名字对得上就算一对。"""
    root = tmp_path / "素材2"
    root.mkdir()
    img(root / "123.jpg")
    img(root / "123-素材图.png", MAT_SIZE)

    result = scan_folder(opts(root))
    assert len(result.rows) == 1
    assert result.rows[0].material_name == "123-素材图.png"


def test_recursive_collect_can_be_turned_off(tmp_path: Path):
    """默认只看所选文件夹这一层——父目录里套着大子树时，递归会慢很多。"""
    root = tmp_path / "父目录"
    (root / "子目录").mkdir(parents=True)
    img(root / "top.jpg")
    img(root / "top-素材图.jpg", MAT_SIZE)
    img(root / "子目录" / "inner.jpg")
    img(root / "子目录" / "inner-素材图.jpg", MAT_SIZE)

    shallow = scan_folder(opts(root))
    assert [r.main_name for r in shallow.rows] == ["top.jpg"]

    deep = scan_folder(opts(root, recursive=True))
    assert sorted(r.main_name for r in deep.rows) == ["inner.jpg", "top.jpg"]


def test_summary_says_which_collection_type_was_used(tmp_path: Path):
    root = tmp_path / "父目录"
    root.mkdir()
    img(root / "a.jpg")

    assert "含子文件夹" not in scan_folder(opts(root)).summary()
    assert "含子文件夹" in scan_folder(opts(root, recursive=True)).summary()


def test_scan_keeps_the_index_for_reuse(src: Path):
    """索引要带回去给调用方复用，否则查主图规格会把整个目录再扫一遍。"""
    result = scan_folder(opts(src))
    assert result.index is not None
    assert len(result.index.files) == result.image_count


def test_force_generate_marks_rows_instead_of_skipping(src: Path):
    result = scan_folder(opts(src, force_gen_material=True))
    names = [r.main_name for r in result.rows]
    assert "09999999999.jpg" in names
    row = next(r for r in result.rows if r.main_name == "09999999999.jpg")
    assert row.generated_material is True
    assert row.material_name == "09999999999-素材图.jpg"


def test_summary_is_readable(src: Path):
    s = scan_folder(opts(src)).summary()
    assert "张图" in s and "行" in s and "跳过" in s


# ------------------------------------------------------------------ 写表
def test_write_produces_the_expected_columns(src: Path):
    result = scan_folder(opts(src))
    out = write_table(result, src / "新表.xlsx")

    wb = openpyxl.load_workbook(out.path, read_only=True)
    ws = wb["Sheet1"]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    header = list(rows[0])
    expected = [schema.EXPECTED_HEADER[i] for i in sorted(schema.EXPECTED_HEADER)]
    assert header == expected
    assert len(header) == 20

    first = rows[1]
    def cell(col):
        return first[col - 1]

    assert cell(schema.COL_FOLDER) == str(src)
    assert cell(schema.COL_MAIN_IMAGE) == "00742093597.jpg"
    assert cell(schema.COL_MATERIAL_IMAGE) == "00742093597-素材图.jpg"
    assert cell(schema.COL_SKU_PREFIX_CODE) == "00742093597"
    assert cell(schema.COL_SHOP) == "A lovely costume"
    assert cell(schema.COL_TEMPLATE_SPU) == "8909735440"
    assert cell(schema.COL_MAIN_IMAGE_INDEX) == 1
    # 这几列留给上架阶段回填，收集时必须是空的
    for col in (schema.COL_PRODUCT_ID, schema.COL_STATUS, schema.COL_TITLE_CN,
                schema.COL_TITLE_EN, schema.COL_SKC_CODE):
        assert cell(col) in ("", None), f"第 {col} 列不该有值"


def test_use_img_name_as_skc_fills_column_19(src: Path):
    result = scan_folder(opts(src, use_img_name_as_skc=True))
    out = write_table(result, src / "带SKC.xlsx")

    wb = openpyxl.load_workbook(out.path, read_only=True)
    rows = list(wb["Sheet1"].iter_rows(values_only=True))
    wb.close()
    assert rows[1][schema.COL_SKC_CODE - 1] == "00742093597"


def test_never_overwrites_an_existing_table(src: Path):
    """素材目录里本来就躺着一张表——绝不能覆盖。"""
    existing = src / "商品信息表.xlsx"
    existing.write_bytes("这是用户原有的表，不许动".encode("utf-8"))
    before = existing.read_bytes()

    result = scan_folder(opts(src))
    out = write_table(result)          # 不指定路径 → 默认就是上面那个名字

    assert out.renamed is True
    assert out.path != existing
    assert existing.read_bytes() == before, "原表被改了！"
    assert out.path.exists()


def test_safe_output_path_keeps_original_when_free(tmp_path: Path):
    target = tmp_path / "新表.xlsx"
    final, renamed = safe_output_path(target)
    assert final == target
    assert renamed is False


def test_generated_material_is_actually_written(src: Path):
    result = scan_folder(opts(src, force_gen_material=True))
    assert not (src / "09999999999-素材图.jpg").exists()

    out = write_table(result, src / "强生成.xlsx", generate_missing_material=True)

    made = src / "09999999999-素材图.jpg"
    assert made.exists(), "勾了强制生成就该真的生成出来"
    assert made.name in out.generated_materials
    with Image.open(made) as im:
        assert im.size == MAT_SIZE


def test_write_without_rows_is_rejected(tmp_path: Path):
    empty = tmp_path / "空目录"
    empty.mkdir()
    with pytest.raises(ValueError):
        write_table(scan_folder(opts(empty)), empty / "x.xlsx")


def test_default_output_lives_in_the_folder(src: Path):
    assert default_output(src) == src / "商品信息表.xlsx"


# ------------------------------------------------------------------ 主图规格预检
def test_check_main_images_flags_bad_sizes(tmp_path: Path):
    """收集时提前发现主图尺寸不合规，别等上架被平台打回。"""
    from temu_bot.collect import check_main_images

    root = tmp_path / "素材"
    root.mkdir()
    img(root / "ok.jpg", (1340, 1785))
    img(root / "ok-素材图.jpg", MAT_SIZE)
    img(root / "short.jpg", (1340, 1784))          # 矮 1 像素，就是真实踩过的坑
    img(root / "short-素材图.jpg", MAT_SIZE)

    problems = check_main_images(scan_folder(opts(root)))

    assert "short.jpg" in problems
    assert any("高" in p for p in problems["short.jpg"])
    assert "ok.jpg" not in problems


def test_check_main_images_is_empty_when_all_good(tmp_path: Path):
    from temu_bot.collect import check_main_images

    root = tmp_path / "素材"
    root.mkdir()
    img(root / "a.jpg", (1340, 1785))
    img(root / "a-素材图.jpg", MAT_SIZE)

    assert check_main_images(scan_folder(opts(root))) == {}


# ------------------------------------------------------------------ 店铺名核对
def test_find_unknown_shop_warns_on_a_typo():
    from temu_bot.collect import find_unknown_shop

    class S:
        name = "A lovely costume"

    class Cfg:
        shops = [S()]

    assert find_unknown_shop(Cfg(), "A lovely costume") == ""
    warn = find_unknown_shop(Cfg(), "A lovely costum")     # 少了一个 e
    assert "不在配置的店铺列表里" in warn
    assert "A lovely costume" in warn


def test_find_unknown_shop_warns_when_blank():
    from temu_bot.collect import find_unknown_shop

    class Cfg:
        shops = []

    assert "还没填店铺名" in find_unknown_shop(Cfg(), "  ")


def test_find_unknown_shop_is_silent_when_nothing_configured():
    """配置里一个店都没有时，不该乱报错。"""
    from temu_bot.collect import find_unknown_shop

    class Cfg:
        shops = []

    assert find_unknown_shop(Cfg(), "任意店名") == ""
