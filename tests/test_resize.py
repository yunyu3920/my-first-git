"""尺寸与格式：默认 1340×1785 / JPG，覆盖原图并保持文件名。"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from temu_bot.toolbox import resize
from temu_bot.toolbox.result import FAILED, OK, SKIPPED, WOULD


def make_image(path: Path, size: tuple[int, int], mode: str = "RGB", color=(200, 30, 30)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new(mode, size, color).save(path)
    return path


def names(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


def test_defaults_are_1340x1785_jpg_and_plan_writes_nothing(tmp_path):
    src = make_image(tmp_path / "a.png", (300, 400))
    before = names(tmp_path)

    report = resize.plan(tmp_path)

    item = report.items[0]
    assert item.status == WOULD
    assert "1340×1785" in report.note and "JPG" in report.note
    assert item.target.endswith("a.jpg"), "覆盖模式保持原名，只把扩展名改成 jpg"
    assert "删掉原文件" in item.detail
    assert names(tmp_path) == before, "预览一个字节都不该写"
    assert src.is_file()


def test_run_overwrites_png_to_same_name_jpg_and_deletes_source(tmp_path):
    src = make_image(tmp_path / "a.png", (300, 400), mode="RGBA", color=(200, 30, 30, 128))

    report = resize.run(tmp_path)

    assert report.ok, report.summary()
    out = tmp_path / "a.jpg"
    assert out.is_file()
    assert not src.exists(), "png 原文件应该被同名 jpg 替代"
    assert names(tmp_path) == ["a.jpg"]
    with Image.open(out) as im:
        assert im.size == (1340, 1785)
        assert im.format == "JPEG"
        assert im.mode == "RGB", "JPG 要把透明铺成白底"


def test_run_overwrites_a_jpg_in_place_without_changing_the_name(tmp_path):
    src = make_image(tmp_path / "a.jpg", (300, 400))

    report = resize.run(tmp_path)

    assert report.ok, report.summary()
    assert names(tmp_path) == ["a.jpg"], "不能多出 _宽x高 副本"
    with Image.open(src) as im:
        assert im.size == (1340, 1785)


def test_contain_keeps_the_whole_image_and_pads_white(tmp_path):
    src = make_image(tmp_path / "a.jpg", (100, 200), color=(200, 30, 30))

    report = resize.run(tmp_path, width=400, height=400, fit="contain")

    assert report.ok, report.summary()
    with Image.open(src) as im:
        assert im.size == (400, 400)
        assert im.getpixel((0, 0)) == (255, 255, 255), "留白应该是白的"
        assert im.getpixel((200, 200))[0] > 150, "中间还是原图"


def test_bad_format_is_reported_not_crashed(tmp_path):
    make_image(tmp_path / "a.jpg", (100, 100))

    report = resize.plan(tmp_path, target_format="gif")

    assert report.items[0].status == FAILED
    assert "不支持" in report.items[0].detail


def test_a_broken_image_is_left_untouched_and_no_tmp_is_left(tmp_path):
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not an image")

    report = resize.run(tmp_path)

    assert report.items[0].status == FAILED
    assert bad.read_bytes() == b"not an image"
    assert not list(tmp_path.glob("*.tmp"))


def test_copy_mode_can_still_write_suffixed_copies(tmp_path):
    src = make_image(tmp_path / "a.png", (100, 100))

    report = resize.run(tmp_path, width=200, height=200, inplace=False)

    assert report.ok, report.summary()
    assert (tmp_path / "a_200x200.jpg").is_file()
    assert src.is_file(), "副本模式不动原图"
    assert names(tmp_path) == ["a.png", "a_200x200.jpg"]


def test_copy_mode_skips_existing_unless_overwrite(tmp_path):
    make_image(tmp_path / "a.jpg", (100, 100))
    assert resize.run(tmp_path, width=200, height=200, inplace=False).ok

    again = resize.run(tmp_path, width=200, height=200, inplace=False)
    assert again.items[0].status == SKIPPED
    assert "已存在" in again.items[0].detail

    forced = resize.run(tmp_path, width=200, height=200, inplace=False, overwrite=True)
    assert forced.items[0].status == OK


def test_copy_mode_respects_output_dir(tmp_path):
    make_image(tmp_path / "src" / "a.jpg", (100, 100))
    out = tmp_path / "out"

    report = resize.run(tmp_path / "src", width=200, height=300, inplace=False, output_dir=out)

    assert report.ok, report.summary()
    assert (out / "a_200x300.jpg").is_file()
    assert (tmp_path / "src" / "a.jpg").is_file()


def test_recursive_overwrites_in_each_folder(tmp_path):
    make_image(tmp_path / "a.jpg", (100, 100))
    make_image(tmp_path / "sub" / "b.jpg", (100, 100))

    report = resize.run(tmp_path, width=200, height=200, recursive=True)

    assert report.ok, report.summary()
    assert (tmp_path / "a.jpg").is_file()
    assert (tmp_path / "sub" / "b.jpg").is_file()
    assert not list(tmp_path.rglob("*.tmp"))
