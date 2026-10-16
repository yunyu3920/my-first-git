"""图片解析测试：表里写的是名字，图在文件夹里；能不能对上，全靠这里。"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from temu_bot.imaging.resolve import (
    AMBIGUOUS,
    CASEFOLD,
    EXACT,
    MISSING,
    PREFIX,
    RECURSE,
    STEM,
    index_folder,
)


def touch_image(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (10, 10), (1, 2, 3)).save(path, format="JPEG", quality=80)
    return path


@pytest.fixture()
def folder(tmp_path: Path) -> Path:
    root = tmp_path / "imgs"
    root.mkdir()
    touch_image(root / "12345.jpg")
    touch_image(root / "ABC.jpg")
    touch_image(root / "67890.png")
    touch_image(root / "99999-素材图.jpg")
    touch_image(root / "sub" / "deep.jpg")
    (root / "readme.txt").write_text("不是图片", encoding="utf-8")
    return root


def test_exact_match(folder: Path):
    got = index_folder(folder).resolve("12345.jpg")
    assert got.ok and got.how == EXACT
    assert got.path.name == "12345.jpg"


def test_case_insensitive(folder: Path):
    got = index_folder(folder).resolve("abc.JPG")
    assert got.ok and got.how == CASEFOLD
    assert got.path.name == "ABC.jpg"


def test_extension_mismatch(folder: Path):
    """表里写 .jpg，文件夹里是 .png —— 扩展名不同也算找到。"""
    got = index_folder(folder).resolve("67890.jpg")
    assert got.ok and got.how == STEM
    assert got.path.name == "67890.png"


def test_found_in_subfolder(folder: Path):
    got = index_folder(folder).resolve("deep.jpg")
    assert got.ok and got.how == RECURSE


def test_unique_prefix(folder: Path):
    """表里只写 ID，文件夹里带后缀——唯一命中才认。"""
    got = index_folder(folder).resolve("99999.jpg")
    assert got.ok and got.how == PREFIX
    assert got.path.name == "99999-素材图.jpg"


def test_missing(folder: Path):
    got = index_folder(folder).resolve("不存在.jpg")
    assert not got.ok and got.how == MISSING
    assert "找不到" in got.describe()


def test_ambiguous_is_never_guessed(tmp_path: Path):
    """前缀能匹配到多张时**不猜**——猜错就是给商品配错图。"""
    root = tmp_path / "imgs"
    root.mkdir()
    touch_image(root / "777-a.jpg")
    touch_image(root / "777-b.jpg")

    got = index_folder(root).resolve("777.jpg")
    assert not got.ok
    assert got.how == AMBIGUOUS
    assert len(got.candidates) == 2
    assert "多张" in got.describe()


def test_non_image_files_are_ignored(tmp_path: Path):
    """不是图片的文件不进索引，免得被当成图上传。"""
    root = tmp_path / "imgs"
    root.mkdir()
    (root / "note.txt").write_text("x", encoding="utf-8")
    index = index_folder(root)
    assert index.files == ()
    assert not index.resolve("note.txt").ok


def test_find_by_stem_refuses_to_prefix_match(tmp_path: Path):
    """回归：用 `123-素材图` 找素材图时，绝不能命中主图 `123.jpg`。

    `resolve()` 的前缀策略是"能唯一命中就认"，于是 `"123-素材图".startswith("123")`
    会让**没有素材图**的行被误判成找到了，而且素材图字段被填成主图本身。
    配对这种语义明确的查找必须走 `find_by_stem`。
    """
    root = tmp_path / "imgs"
    root.mkdir()
    touch_image(root / "123.jpg")
    index = index_folder(root)

    assert index.find_by_stem("123-素材图").how == MISSING
    assert not index.find_by_stem("123-素材图").ok


def test_find_by_stem_matches_other_extension(tmp_path: Path):
    root = tmp_path / "imgs"
    root.mkdir()
    touch_image(root / "123.jpg")
    touch_image(root / "123-素材图.png")

    got = index_folder(root).find_by_stem("123-素材图", prefer_suffix=".jpg")
    assert got.ok
    assert got.path.name == "123-素材图.png"


def test_find_by_stem_prefers_the_same_extension(tmp_path: Path):
    root = tmp_path / "imgs"
    root.mkdir()
    touch_image(root / "123-素材图.jpg")
    touch_image(root / "123-素材图.png")

    got = index_folder(root).find_by_stem("123-素材图", prefer_suffix=".png")
    assert got.ok
    assert got.path.name == "123-素材图.png"


def test_find_by_stem_reports_ambiguity_without_a_hint(tmp_path: Path):
    root = tmp_path / "imgs"
    root.mkdir()
    touch_image(root / "123-素材图.jpg")
    touch_image(root / "123-素材图.png")

    got = index_folder(root).find_by_stem("123-素材图")
    assert not got.ok
    assert got.how == AMBIGUOUS


def test_missing_folder_is_reported(tmp_path: Path):
    index = index_folder(tmp_path / "根本没有这个目录")
    assert not index.exists
    got = index.resolve("a.jpg")
    assert not got.ok and got.how == MISSING


def test_blank_name_is_missing(folder: Path):
    assert index_folder(folder).resolve("   ").how == MISSING
