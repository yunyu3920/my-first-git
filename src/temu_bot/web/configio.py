"""控制台的配置读写与只读目录浏览。

两条硬规矩：
1. **只写 `config.local.yaml`**，绝不重写主 `config.yaml`（那文件里全是注释，
   用 yaml.safe_dump 一回写就把注释全抹了）。local 层本来就是设计好的本机覆盖层。
2. **落盘前先整体验证**：把「主配置 + 新的 local」合并起来构造一次 `Config`，
   验不过就直接拒绝，不写坏文件。
"""

from __future__ import annotations

import logging
import os
import string
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from filelock import FileLock, Timeout

from temu_bot.config import Config, config_paths, deep_merge, read_yaml
from temu_bot.imaging.resolve import IMAGE_EXTS

log = logging.getLogger(__name__)

# 允许在网页上改的顶层键白名单——防止请求里塞进奇怪的东西
ALLOWED_TOP_LEVEL = frozenset({"project", "toolbox", "sheet", "browser", "shops", "ai", "runtime"})

# 目录浏览一次最多列多少个（避免超大目录把页面拖死）
LIST_LIMIT = 500


class ConfigWriteError(RuntimeError):
    """配置写入被拒绝（校验不过 / 锁不到 / 键不在白名单）。"""


# ------------------------------------------------------------------ 读摘要
def ai_secret_status(cfg: Config) -> list[dict[str, Any]]:
    """AI 密钥状态：**只报环境变量设没设，绝不回原值**。"""
    out: list[dict[str, Any]] = []
    for provider in cfg.ai.providers:
        env_name = provider.api_key_env
        out.append(
            {
                "id": provider.id,
                "model": provider.model,
                "base_url": provider.base_url,
                "api_key_env": env_name,
                "key_set": bool(os.environ.get(env_name, "").strip()),
            }
        )
    return out


def config_summary(cfg: Config) -> dict[str, Any]:
    """给设置页用的配置摘要（全部是可安全展示的内容）。"""
    main, local = config_paths(cfg.root)
    return {
        "paths": {
            "main": str(main),
            "local": str(local),
            "local_exists": local.exists(),
            "root": str(cfg.root),
        },
        "project": {"table": str(cfg.project.table), "sheet_name": cfg.project.sheet_name},
        "toolbox": {"app_dir": str(cfg.toolbox.app_dir), "ledger": str(cfg.toolbox.ledger)},
        "runtime": cfg.runtime.model_dump(),
        "browser": cfg.browser.model_dump(mode="json"),
        "sheet": {
            "header_row": cfg.sheet.header_row,
            "statuses": dict(cfg.sheet.statuses),
            "backup_keep": cfg.sheet.backup_keep,
        },
        "shops": [
            {
                "name": s.name,
                "login": s.login or "",
                "expected_dxm_shops": list(s.expected_dxm_shops),
                "template_spuid": s.template_spuid or "",
                "profile_key": s.profile_key,
            }
            for s in cfg.shops
        ],
        "ai": {
            "chain": list(cfg.ai.chain),
            "title_max_len": cfg.ai.title_max_len,
            "providers": ai_secret_status(cfg),
        },
    }


# ------------------------------------------------------------------ 写回
def write_local_config(cfg: Config, changes: dict[str, Any]) -> Path:
    """把改动合并进 `config.local.yaml`（保留原有未知键），落盘走原子替换。

    校验不过就抛 `ConfigWriteError`，**不写任何东西**。
    """
    if not isinstance(changes, dict) or not changes:
        raise ConfigWriteError("没有要改的内容")

    bad = set(changes) - ALLOWED_TOP_LEVEL
    if bad:
        raise ConfigWriteError(f"这些键不允许在网页上改：{sorted(bad)}")

    from temu_bot.config import Config as ConfigModel

    main, local = config_paths(cfg.root)

    current_local = read_yaml(local)
    merged_local = deep_merge(current_local, changes)

    # 先验证：主配置 + 新 local 能不能构成一个合法 Config
    try:
        candidate = deep_merge(read_yaml(main), merged_local)
        ConfigModel(**candidate)
    except Exception as exc:  # noqa: BLE001
        raise ConfigWriteError(f"配置校验不通过，已拒绝写入：{exc}") from exc

    payload = yaml.safe_dump(merged_local, allow_unicode=True, sort_keys=False, default_flow_style=False)

    local.parent.mkdir(parents=True, exist_ok=True)
    lock_file = cfg.data_dir / "state" / "config.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    try:
        with FileLock(lock_file, timeout=10):
            # 写坏 local 会**同时废掉 CLI 和网页**的启动，所以先留一份
            if local.exists():
                _backup(local, cfg.data_dir / "backups")

            tmp = local.with_name(f"{local.name}.{os.getpid()}.tmp")
            try:
                with tmp.open("w", encoding="utf-8", newline="\n") as fh:
                    fh.write(payload)
                    fh.flush()
                    os.fsync(fh.fileno())          # 落盘再替换，断电也不会半截
                os.replace(tmp, local)
            finally:
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
    except Timeout as exc:
        raise ConfigWriteError(f"另一个进程正在写配置，等锁超时：{lock_file}") from exc

    log.info("配置已写入 %s（改动键：%s）", local, sorted(changes))
    return local


# ------------------------------------------------------------------ 目录浏览
@dataclass(slots=True)
class DirEntry:
    name: str
    path: str
    is_dir: bool
    size: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "path": self.path, "is_dir": self.is_dir, "size": self.size}


def _backup(path: Path, backup_dir: Path, keep: int = 10) -> Path | None:
    """留一份带时间戳的副本（沿用 guard.backup 的口径，保留最近 N 份）。"""
    import shutil
    from datetime import datetime

    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = backup_dir / f"{path.stem}.{stamp}.bak{path.suffix}"
        shutil.copy2(path, dest)
        old = sorted(backup_dir.glob(f"{path.stem}.*.bak{path.suffix}"), key=lambda p: p.stat().st_mtime)
        for stale in old[:-keep] if keep > 0 else []:
            try:
                stale.unlink()
            except OSError:
                pass
        return dest
    except OSError as exc:
        # 备份失败不该拦住保存本身（local.yaml 本来就是"丢了也能重建"的覆盖层）
        log.warning("配置备份失败（继续保存）：%s", exc)
        return None


def list_drives() -> list[DirEntry]:
    """Windows 下列出可用盘符（path 为空时用）。"""
    out: list[DirEntry] = []
    for letter in string.ascii_uppercase:
        root = Path(f"{letter}:\\")
        try:
            if root.exists():
                out.append(DirEntry(name=f"{letter}:\\", path=str(root), is_dir=True))
        except OSError:
            continue
    return out


def list_dir(path: str | None, *, images_only: bool = False) -> dict[str, Any]:
    """列一个目录（**只读**）。path 为空则列盘符。

    返回 {path, parent, dirs:[...], files:[...], truncated, error}。
    权限不足之类的错误放在 `error` 里回给前端，不抛异常。
    """
    if not path:
        return {
            "path": "",
            "parent": None,
            "dirs": [e.as_dict() for e in list_drives()],
            "files": [],
            "truncated": False,
            "error": "",
        }

    target = Path(path)
    if not target.exists():
        return {"path": str(target), "parent": None, "dirs": [], "files": [], "truncated": False,
                "error": f"路径不存在：{target}"}
    if not target.is_dir():
        return {"path": str(target), "parent": str(target.parent), "dirs": [], "files": [],
                "truncated": False, "error": f"这不是一个文件夹：{target}"}

    dirs: list[DirEntry] = []
    files: list[DirEntry] = []
    truncated = False
    try:
        for child in sorted(target.iterdir(), key=lambda p: p.name.lower()):
            if len(dirs) + len(files) >= LIST_LIMIT:
                truncated = True
                break
            try:
                if child.is_dir():
                    dirs.append(DirEntry(name=child.name, path=str(child), is_dir=True))
                elif child.is_file():
                    if images_only and child.suffix.lower() not in IMAGE_EXTS:
                        continue
                    files.append(
                        DirEntry(
                            name=child.name,
                            path=str(child),
                            is_dir=False,
                            size=child.stat().st_size,
                        )
                    )
            except OSError:
                continue
    except PermissionError:
        return {"path": str(target), "parent": str(target.parent), "dirs": [], "files": [],
                "truncated": False, "error": f"没有权限读取：{target}"}

    dirs.sort(key=lambda e: e.name.lower())
    files.sort(key=lambda e: e.name.lower())
    parent = str(target.parent) if target.parent != target else None
    return {
        "path": str(target),
        "parent": parent,
        "dirs": [e.as_dict() for e in dirs],
        "files": [e.as_dict() for e in files],
        "truncated": truncated,
        "error": "",
    }
