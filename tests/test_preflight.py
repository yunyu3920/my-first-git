"""上架前输入校验测试：表格和图片文件夹，少一样、对不上，都必须先挡下来。"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from temu_bot.preflight import check_inputs
from temu_bot.sheet.table import SheetTable


def touch_image(path: Path, size=(1340, 1785)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (10, 20, 30)).save(path, format="JPEG", quality=80)
    return path


@pytest.fixture()
def imgs(tmp_path: Path) -> Path:
    root = tmp_path / "imgs"
    root.mkdir()
    touch_image(root / "a01.jpg")
    touch_image(root / "a01-素材图.jpg", (800, 800))
    touch_image(root / "b02.jpg")
    touch_image(root / "b02-素材图.jpg", (800, 800))
    return root


def write_table(path: Path, rows: list[dict[int, object]]) -> Path:
    from tests.conftest import make_table

    return make_table(path, rows)


@pytest.fixture()
def cfg_for(tmp_path: Path, imgs: Path):
    def build(rows: list[dict[int, object]]):
        from temu_bot.config import Config

        table = write_table(tmp_path / "表.xlsx", rows)
        cfg = Config(
            project={"table": table, "sheet_name": "Sheet1"},
            toolbox={"app_dir": tmp_path / "tb", "ledger": tmp_path / "tb" / "used.txt"},
        )
        cfg.root = tmp_path
        cfg.data_dir = tmp_path / "data"
        cfg.work_dir = tmp_path / "work"
        cfg.ensure_dirs()
        return cfg

    return build


def row_for(folder: Path, main: str, material: str, shop="A lovely costume") -> dict[int, object]:
    return {
        1: str(folder),
        2: main,
        8: material,
        9: "00742093597",       # SKU 货号前缀编码：上架必填
        10: shop,
        11: "8909735440",
        17: "标题",
        18: "Title",
    }


def test_all_inputs_present_passes(cfg_for, imgs: Path):
    cfg = cfg_for([row_for(imgs, "a01.jpg", "a01-素材图.jpg")])
    report = check_inputs(cfg)

    assert report.ok
    assert report.table_ok and report.folder_ok
    assert report.image_count == 4
    assert len(report.rows) == 1 and report.rows[0].ok


def test_missing_table_is_reported(tmp_path: Path, cfg_for, imgs: Path):
    cfg = cfg_for([row_for(imgs, "a01.jpg", "a01-素材图.jpg")])
    cfg.project.table = tmp_path / "没有这张表.xlsx"

    report = check_inputs(cfg)
    assert not report.ok
    assert "找不到商品表" in report.table_detail


def test_missing_images_dir_is_reported(cfg_for, tmp_path: Path, imgs: Path):
    cfg = cfg_for([row_for(imgs, "a01.jpg", "a01-素材图.jpg")])
    report = check_inputs(cfg, images_dir=tmp_path / "这个文件夹不存在")

    assert not report.ok
    assert not report.folder_ok
    assert "不存在" in report.folder_detail


def test_empty_images_dir_is_reported(cfg_for, tmp_path: Path, imgs: Path):
    empty = tmp_path / "空的"
    empty.mkdir()
    cfg = cfg_for([row_for(imgs, "a01.jpg", "a01-素材图.jpg")])

    report = check_inputs(cfg, images_dir=empty)
    assert not report.ok
    assert "一张图都没有" in report.folder_detail


def test_row_with_unfindable_image_is_blocked(cfg_for, imgs: Path):
    """表里写了图名但文件夹里没有 → 这一行必须在碰接口前被挡下来。"""
    cfg = cfg_for(
        [
            row_for(imgs, "a01.jpg", "a01-素材图.jpg"),
            row_for(imgs, "根本没有.jpg", "b02-素材图.jpg"),
        ]
    )
    report = check_inputs(cfg)

    assert not report.ok
    assert len(report.ready) == 1
    assert len(report.blocked) == 1
    assert report.blocked[0].row_index == 3
    assert any("找不到" in p for p in report.blocked[0].problems)


def test_row_without_material_image_is_blocked(cfg_for, imgs: Path):
    """只有主图、没填素材图 → **挡下来**。

    规矩：一行 = 主图 + 素材图这一对。缺素材图的话素材图位会留着模板那张，
    上出来是个"半对"的商品——收集图片时本来就把这种行跳过，上架这里也得一致。
    （以前这里写的是"允许"，跟上架流程的 PRECHECK 对不上：控制台会说"可上架"，
      真跑却被挡。两边现在一致了。）
    """
    cfg = cfg_for([row_for(imgs, "a01.jpg", "")])
    report = check_inputs(cfg)

    assert not report.ok
    assert len(report.blocked) == 1
    assert any("素材图" in p for p in report.blocked[0].problems)


def test_row_without_sku_code_is_blocked(cfg_for, imgs: Path):
    """第 9 列是空的 → 挡下来（没有它就只能沿用模板的货号）。"""
    row = row_for(imgs, "a01.jpg", "a01-素材图.jpg")
    row.pop(9)
    report = check_inputs(cfg_for([row]))

    assert not report.ok
    assert any("第 9 列" in p for p in report.blocked[0].problems)


def test_table_without_valid_rows_is_reported(cfg_for, imgs: Path):
    """连「文件夹 + 主图」都没填的行不算数据行，表里等于没有可上架的。"""
    cfg = cfg_for([{10: "A lovely costume", 17: "只有标题"}])
    report = check_inputs(cfg)

    assert not report.ok
    assert not report.table_ok
    assert "一行" in report.table_detail


def test_images_dir_overrides_table_column(cfg_for, tmp_path: Path, imgs: Path):
    """--images-dir 给了就用它，表里第 1 列写错也没关系。"""
    other = tmp_path / "另一批图"
    other.mkdir()
    touch_image(other / "c03.jpg")

    cfg = cfg_for([row_for(tmp_path / "根本不存在的目录", "c03.jpg", "c03.jpg")])
    report = check_inputs(cfg, images_dir=other)

    assert report.folder_ok
    assert report.rows[0].resolved[0].ok


def test_resolution_records_how_it_was_found(cfg_for, tmp_path: Path, imgs: Path):
    (imgs / "a01.png").write_bytes((imgs / "a01.jpg").read_bytes())
    cfg = cfg_for([row_for(imgs, "A01.JPG", "a01-素材图.jpg")])
    report = check_inputs(cfg)

    got = report.rows[0].resolved[0]
    assert got.ok
    assert got.how != "exact"          # 大小写不同，靠 casefold 找到的
