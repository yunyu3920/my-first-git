"""新用户没配商品表时：自动在桌面附近找一张（但用户明确设过就不猜）。"""

from __future__ import annotations

import os
from pathlib import Path

from temu_bot.config import autodetect_table, load_config


def make_file(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path


def test_autodetect_finds_a_table_in_a_desktop_subfolder(tmp_path: Path):
    root = tmp_path / "app"
    home = tmp_path / "home"
    target = make_file(home / "Desktop" / "黑色素材" / "商品信息表.xlsx")

    assert autodetect_table(root, home) == target


def test_autodetect_prefers_the_newest_when_there_are_several(tmp_path: Path):
    root = tmp_path / "app"
    home = tmp_path / "home"
    old = make_file(home / "Desktop" / "旧表" / "商品信息表.xlsx")
    new = make_file(home / "Desktop" / "新表" / "商品信息表.xlsx")
    os.utime(old, (1, 1))
    os.utime(new, (2, 2))

    assert autodetect_table(root, home) == new


def test_autodetect_returns_none_when_there_is_no_table(tmp_path: Path):
    root = tmp_path / "app"
    home = tmp_path / "home"
    home.mkdir()

    assert autodetect_table(root, home) is None


def _write_main(tmp_path: Path) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    main = config_dir / "config.yaml"
    main.write_text(
        "\n".join(
            [
                "project:",
                f"  table: {tmp_path / 'missing.xlsx'}",
                "toolbox:",
                f"  app_dir: {tmp_path / 'tb'}",
                f"  ledger: {tmp_path / 'tb' / 'used_ids.txt'}",
            ]
        ),
        encoding="utf-8",
    )
    return main


def test_load_config_autodetects_when_there_is_no_local_override(tmp_path, monkeypatch):
    main = _write_main(tmp_path)
    monkeypatch.setenv("TEMU_BOT_CONFIG", str(main))
    detected = make_file(tmp_path / "found.xlsx")
    monkeypatch.setattr("temu_bot.config.autodetect_table", lambda root: detected)

    assert load_config().project.table == detected


def test_config_without_a_toolbox_block_uses_safe_defaults(tmp_path, monkeypatch):
    """图片批处理已内置：配置里整块省略 toolbox 也能启动，不会去读一个目录当台账。"""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    main = config_dir / "config.yaml"
    main.write_text(
        f"project:\n  table: {tmp_path / 'missing.xlsx'}\n", encoding="utf-8"
    )
    monkeypatch.setenv("TEMU_BOT_CONFIG", str(main))

    cfg = load_config()

    assert cfg.toolbox_seed_ledgers == []
    assert "legacy" in str(cfg.toolbox.ledger)


def test_load_config_does_not_override_an_explicit_local_table(tmp_path, monkeypatch):
    main = _write_main(tmp_path)
    explicit = tmp_path / "我自己选的表.xlsx"
    (main.parent / "config.local.yaml").write_text(
        f"project:\n  table: {explicit}\n", encoding="utf-8"
    )
    monkeypatch.setenv("TEMU_BOT_CONFIG", str(main))
    monkeypatch.setattr(
        "temu_bot.config.autodetect_table",
        lambda root: (_ for _ in ()).throw(AssertionError("不该自动猜表")),
    )

    assert load_config().project.table == explicit
