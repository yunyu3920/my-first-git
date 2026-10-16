"""控制台配置读写测试：只动 local 层、保留未知键、验不过就不写。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from temu_bot.config import Config, config_paths, load_config
from temu_bot.web.configio import (
    ALLOWED_TOP_LEVEL,
    ConfigWriteError,
    config_summary,
    list_dir,
    write_local_config,
)


@pytest.fixture()
def project(tmp_path: Path, monkeypatch):
    """造一个独立小项目：主配置 + （之后才有的）local 层。"""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    main = config_dir / "config.yaml"
    main.write_text(
        "\n".join(
            [
                "# 这行注释绝不能被抹掉",
                "project:",
                f"  table: {tmp_path / '商品信息表.xlsx'}",
                "  sheet_name: Sheet1",
                "toolbox:",
                f"  app_dir: {tmp_path / 'tb'}",
                f"  ledger: {tmp_path / 'tb' / 'used_ids.txt'}",
                "runtime:",
                "  speed: cautious",
                "  log_level: INFO",
                "shops:",
                "  - name: A hat tshirt",
                "    login: hat-tshirt",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TEMU_BOT_CONFIG", str(main))
    cfg = load_config()
    cfg.root = tmp_path
    cfg.data_dir = tmp_path / "data"
    cfg.work_dir = tmp_path / "work"
    cfg.ensure_dirs()
    return {"cfg": cfg, "main": main, "root": tmp_path}


def test_write_goes_to_local_not_main(project):
    cfg = project["cfg"]
    write_local_config(cfg, {"runtime": {"speed": "normal"}})

    main, local = config_paths(cfg.root)
    assert local.exists()
    assert yaml.safe_load(local.read_text(encoding="utf-8"))["runtime"]["speed"] == "normal"
    # 主配置必须一字未改（注释也要在）
    assert "这行注释绝不能被抹掉" in main.read_text(encoding="utf-8")


def test_reload_sees_the_change(project):
    cfg = project["cfg"]
    write_local_config(cfg, {"runtime": {"speed": "fast"}})
    assert load_config().runtime.speed == "fast"


def test_unknown_keys_in_local_are_preserved(project):
    cfg = project["cfg"]
    _, local = config_paths(cfg.root)
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text("my_own_note: 别删我\nruntime:\n  speed: normal\n", encoding="utf-8")

    write_local_config(cfg, {"runtime": {"log_level": "DEBUG"}})

    data = yaml.safe_load(local.read_text(encoding="utf-8"))
    assert data["my_own_note"] == "别删我"
    assert data["runtime"]["speed"] == "normal"       # 已有改动保留
    assert data["runtime"]["log_level"] == "DEBUG"    # 新改动合并进去


def test_invalid_value_is_rejected_and_nothing_is_written(project):
    cfg = project["cfg"]
    _, local = config_paths(cfg.root)

    with pytest.raises(ConfigWriteError):
        write_local_config(cfg, {"runtime": {"speed": "光速"}})

    assert not local.exists()
    assert load_config().runtime.speed == "cautious"


def test_disallowed_top_level_key_is_rejected(project):
    cfg = project["cfg"]
    with pytest.raises(ConfigWriteError):
        write_local_config(cfg, {"__evil__": {"x": 1}})
    assert "__evil__" not in ALLOWED_TOP_LEVEL


def test_empty_changes_is_rejected(project):
    with pytest.raises(ConfigWriteError):
        write_local_config(project["cfg"], {})


def test_no_temp_file_left_behind(project):
    cfg = project["cfg"]
    write_local_config(cfg, {"runtime": {"speed": "normal"}})
    assert list(cfg.root.glob("**/*.tmp")) == []


def test_backup_is_created_on_second_write(project):
    cfg = project["cfg"]
    write_local_config(cfg, {"runtime": {"speed": "normal"}})
    write_local_config(cfg, {"runtime": {"speed": "fast"}})

    # 命名沿用 guard.backup 的口径：{stem}.{stamp}.bak{suffix}
    backups = list((cfg.data_dir / "backups").glob("config.local.*.bak.yaml"))
    assert backups, "第二次写入前应该留一份备份"


def test_windows_paths_survive_the_round_trip(project, tmp_path: Path):
    """Path 字段不能被 yaml.safe_dump 拒掉，反斜杠也不能被转义坏。"""
    cfg = project["cfg"]
    target = tmp_path / "新的 商品信息表.xlsx"

    write_local_config(cfg, {"project": {"table": str(target)}})
    data = yaml.safe_load(config_paths(cfg.root)[1].read_text(encoding="utf-8"))
    assert data["project"]["table"] == str(target)
    assert load_config().project.table == target


def test_config_summary_hides_secrets(project, monkeypatch):
    cfg = project["cfg"]
    monkeypatch.setenv("TEMU_BOT_AI_ZHIPU_KEY", "super-secret-value")

    from temu_bot.config import AiCfg, AiProviderCfg

    cfg.ai = AiCfg(
        chain=["zhipu"],
        providers=[
            AiProviderCfg(
                id="zhipu",
                base_url="https://example.invalid",
                model="glm-4v-flash",
                api_key_env="TEMU_BOT_AI_ZHIPU_KEY",
            )
        ],
    )
    summary = config_summary(cfg)
    blob = yaml.safe_dump(summary, allow_unicode=True)

    assert "super-secret-value" not in blob
    provider = summary["ai"]["providers"][0]
    assert provider["key_set"] is True
    assert "api_key" not in provider or provider.get("api_key") is None


# ------------------------------------------------------------------ 目录浏览
def test_list_dir_lists_dirs_and_files(tmp_path: Path):
    (tmp_path / "子目录").mkdir()
    (tmp_path / "a.jpg").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("hi", encoding="utf-8")

    data = list_dir(str(tmp_path))
    assert data["error"] == ""
    assert [d["name"] for d in data["dirs"]] == ["子目录"]
    assert sorted(f["name"] for f in data["files"]) == ["a.jpg", "notes.txt"]
    assert data["parent"] == str(tmp_path.parent)


def test_list_dir_images_only(tmp_path: Path):
    (tmp_path / "a.jpg").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("hi", encoding="utf-8")

    data = list_dir(str(tmp_path), images_only=True)
    assert [f["name"] for f in data["files"]] == ["a.jpg"]


def test_list_dir_missing_path_reports_error(tmp_path: Path):
    data = list_dir(str(tmp_path / "没有这个目录"))
    assert "不存在" in data["error"]
    assert data["dirs"] == []


def test_list_dir_on_a_file_reports_error(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    assert "不是一个文件夹" in list_dir(str(f))["error"]


def test_list_dir_empty_path_lists_drives():
    data = list_dir("")
    assert data["error"] == ""
    if os.name == "nt":
        assert data["dirs"], "Windows 上应该至少列出一个盘符"
        assert all(d["is_dir"] for d in data["dirs"])
