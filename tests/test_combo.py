"""尺寸与格式 + 11 位 ID 一条龙：顺序、产出、进度。"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from temu_bot.toolbox import combo
from temu_bot.web.combo_jobs import ComboRequest, ComboRunner


def make_image(path: Path, size: tuple[int, int], color=(200, 30, 30)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)
    return path


def test_combo_does_resize_first_then_id_and_lists(sandbox, tmp_path):
    folder = tmp_path / "imgs"
    make_image(folder / "a.jpg", (300, 400))
    make_image(folder / "b.png", (100, 100), color=(10, 200, 10))

    events: list[tuple] = []
    report = combo.run(
        sandbox["cfg"],
        folder,
        export_format="both",
        on_progress=lambda i, total, src: events.append(("progress", i, total)),
        on_stage=lambda name: events.append(("stage", name)),
    )

    assert report.ok, report.summary()
    images = [p for p in folder.iterdir() if p.suffix.lower() == ".jpg"]
    assert len(images) == 2
    for path in images:
        assert len(path.stem) == 11 and path.stem.isdigit(), "第二步应该还原成纯 11 位 ID"
        with Image.open(path) as im:
            assert im.size == (1340, 1785)
    assert (folder / "图片文件名清单.csv").is_file()
    assert (folder / "图片文件名清单.xlsx").is_file()

    # 产出只列第二步的清单/台账，不能把尺寸与格式的「原图路径」也列出来
    assert not any(Path(p).suffix.lower() in (".jpg", ".png") for p in report.outputs)
    assert ("stage", "rename") in events
    assert any(e[0] == "progress" for e in events)


def test_combo_list_output_can_be_the_image_folder(sandbox, tmp_path):
    """「清单输出路径」填图片文件夹本身时，清单写进这个目录，文件夹不能改名。"""
    folder = tmp_path / "imgs"
    make_image(folder / "a.jpg", (100, 100))

    report = combo.run(sandbox["cfg"], folder, csv_path=folder, export_format="both")

    assert report.ok, report.summary()
    assert (folder / "图片文件名清单.csv").is_file()
    assert (folder / "图片文件名清单.xlsx").is_file()
    assert folder.is_dir()
    assert not list(tmp_path.glob("imgs.bak-*"))


def test_combo_plan_writes_nothing(sandbox, tmp_path):
    folder = tmp_path / "imgs"
    make_image(folder / "a.jpg", (100, 100))
    before = sorted(p.name for p in folder.iterdir())

    report = combo.plan(sandbox["cfg"], folder)

    assert report.items
    assert sorted(p.name for p in folder.iterdir()) == before


def test_combo_plan_does_not_open_every_image(monkeypatch, sandbox, tmp_path):
    """大文件夹预览不能逐张打开图片（否则看起来像卡死）。"""
    from temu_bot.toolbox import resize as resize_mod

    folder = tmp_path / "imgs"
    make_image(folder / "a.jpg", (100, 100))

    def boom(_src):
        raise AssertionError("预览不应该逐张打开图片")

    monkeypatch.setattr(resize_mod, "probe_one", boom)

    report = combo.plan(sandbox["cfg"], folder)
    assert report.items


def test_combo_runner_reports_progress_and_result(sandbox, tmp_path):
    folder = tmp_path / "imgs"
    make_image(folder / "a.jpg", (100, 100))

    runner = ComboRunner()
    runner.start(sandbox["cfg"], ComboRequest(folder=str(folder)))
    runner.wait(timeout=30)

    snap = runner.snapshot()
    assert snap["state"] == "done", snap
    assert snap["percent"] == 100
    assert snap["result"]["ok"] is True
    assert snap["result"]["folder"] == str(folder)
