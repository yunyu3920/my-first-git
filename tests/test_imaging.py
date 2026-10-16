"""图片管线测试：钉住"原图只读""产出必须合规"这两条底线。

规格来自平台报错原文：3:4、宽>=1340、高>=1785、<=2MB。
表里 68 行 1340x1785、29 行 1340x1786、1 行 1340x1784（就是失败的那行）。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from PIL import Image

from temu_bot.imaging import MAIN_IMAGE, MATERIAL_IMAGE, inspect, problems_of, process_row
from temu_bot.imaging.pipeline import COVER, PASS, plan_row
from temu_bot.sheet.schema import SheetRow, make_row_key


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_image(path: Path, size: tuple[int, int], color=(200, 30, 30)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, format="JPEG", quality=95)
    return path


# ------------------------------------------------------------------ 规格判定
def test_ratio_tolerance_matches_real_data():
    """1340x1784 失败、1340x1785 与 1340x1786 通过——真实数据的分布就是这么分的。"""
    assert problems_of(1340, 1784, 300_000, MAIN_IMAGE)       # 矮 1 像素 → 不合格
    assert not problems_of(1340, 1785, 300_000, MAIN_IMAGE)
    assert not problems_of(1340, 1786, 300_000, MAIN_IMAGE)


def test_oversize_bytes_is_flagged():
    assert problems_of(1340, 1785, 3 * 1024 * 1024, MAIN_IMAGE)
    assert not problems_of(1340, 1785, 2 * 1024 * 1024, MAIN_IMAGE)


def test_ratio_far_off_is_flagged():
    problems = problems_of(800, 800, 100_000, MAIN_IMAGE)
    assert any("比例" in p for p in problems)
    assert any("高" in p for p in problems)


def test_inspect_missing_file():
    info = inspect(Path("Z:/definitely/not/here.jpg"), MAIN_IMAGE)
    assert not info.exists
    assert not info.ok


# ------------------------------------------------------------------ 计划
@pytest.fixture()
def img_row(tmp_path: Path) -> SheetRow:
    folder = tmp_path / "imgs"
    make_image(folder / "good.jpg", (1340, 1785))
    make_image(folder / "short.jpg", (1340, 1784))
    make_image(folder / "square.jpg", (800, 800))
    return SheetRow(
        row_index=2,
        row_key=make_row_key(str(folder), "short.jpg", "8909735440"),
        folder=str(folder),
        images={"main": ["short.jpg"], "material": ["square.jpg"]},
        shop="A lovely costume",
        template_spu="8909735440",
        main_image_index=1,
        title_cn="男士短袖T恤",
        title_en="Men's T-Shirt",
    )


def test_plan_marks_compliant_as_pass(sandbox, img_row):
    img_row.images = {"main": ["good.jpg"]}
    planned = plan_row(sandbox["cfg"], img_row)
    assert len(planned) == 1
    assert planned[0].action == PASS


def test_plan_marks_short_image_for_cover(sandbox, img_row):
    """1340x1784 要重出；而 800x800 正是素材图规格，本来就合规，不该白折腾。"""
    planned = plan_row(sandbox["cfg"], img_row)
    by_slot = {p.slot: p for p in planned}
    assert by_slot["main"].action == COVER
    assert by_slot["material"].action == PASS


def test_dry_run_writes_nothing(sandbox, img_row):
    result = process_row(sandbox["cfg"], img_row, dry_run=True)
    assert not result.out_dir.exists()
    assert all(not i.dst.exists() for i in result.items)


# ------------------------------------------------------------------ 产出
def test_process_produces_compliant_images_and_leaves_originals_alone(sandbox, img_row):
    folder = Path(img_row.folder)
    before = {p.name: sha(p) for p in folder.iterdir()}

    result = process_row(sandbox["cfg"], img_row, dry_run=False)

    assert not result.failed, [i.line() for i in result.items]
    # 原图一个字节都不能变
    after = {p.name: sha(p) for p in folder.iterdir()}
    assert after == before

    # 产出在 work/<row_key>/out/ 下，且都合规
    assert result.out_dir == sandbox["cfg"].work_dir / img_row.row_key / "out"
    for item in result.items:
        assert item.dst.exists()
        assert item.dst.parent == result.out_dir
        info = inspect(item.dst, item.spec)
        assert info.ok, f"{item.dst.name}: {info.problems}"

    main_out = next(i for i in result.items if i.slot == "main")
    assert main_out.after.size_label() == "1340x1785"

    material_out = next(i for i in result.items if i.slot == "material")
    assert material_out.after.size_label() == "800x800"


def test_compliant_source_is_copied_byte_for_byte(sandbox, img_row):
    """已合规就直通：不能重新编码，否则白丢一次画质。"""
    img_row.images = {"main": ["good.jpg"]}
    result = process_row(sandbox["cfg"], img_row, dry_run=False)

    src = Path(img_row.folder) / "good.jpg"
    out = result.items[0].dst
    assert sha(out) == sha(src)


def test_manifest_is_written(sandbox, img_row):
    import json

    result = process_row(sandbox["cfg"], img_row, dry_run=False)
    manifest = json.loads((result.out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["row_key"] == img_row.row_key
    assert len(manifest["items"]) == 2
    assert all(item["after"]["problems"] == [] for item in manifest["items"])


def test_contain_pads_with_background(sandbox, img_row):
    """800x800 的方图放进 800x800 会原样；竖向长图才需要补边。"""
    folder = Path(img_row.folder)
    make_image(folder / "tall.jpg", (400, 2000))     # 又高又窄
    img_row.images = {"material": ["tall.jpg"]}

    result = process_row(sandbox["cfg"], img_row, dry_run=False)
    item = result.items[0]
    assert item.after.size_label() == "800x800"
    assert item.after.ok


def test_oversized_output_gets_compressed_under_limit(sandbox, img_row, tmp_path):
    """噪声图很难压：必须靠降质阶梯压到 2MB 以内。"""
    import random

    rnd = random.Random(1234)
    noisy = Image.new("RGB", (2400, 3200))
    noisy.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)) for _ in range(2400 * 3200)])
    src = Path(img_row.folder) / "noisy.jpg"
    noisy.save(src, format="JPEG", quality=100)
    assert src.stat().st_size > 2 * 1024 * 1024      # 先确认它真的超大

    img_row.images = {"main": ["noisy.jpg"]}
    result = process_row(sandbox["cfg"], img_row, dry_run=False)

    item = result.items[0]
    assert item.after.ok, item.after.problems
    assert item.after.nbytes <= MAIN_IMAGE.max_bytes


def test_process_reports_missing_source_without_writing(sandbox, img_row):
    img_row.images = {"main": ["不存在的图.jpg"]}
    result = process_row(sandbox["cfg"], img_row, dry_run=False)
    assert result.failed
    assert not result.out_dir.exists()


def test_material_spec_is_marked_ungrounded():
    """素材图规格是从数据反推的，代码里必须标明，别当成平台原文。"""
    assert MATERIAL_IMAGE.grounded is False
    assert MAIN_IMAGE.grounded is True
    assert "推定" in MATERIAL_IMAGE.label_with_grounding
