"""11 位唯一 ID 重命名（对应桌面工具 `Core.ps1` 的 Invoke-SkuRename）。

四个模式：

| 模式 | 名字 | 结果 |
|---|---|---|
| `id-only`    | 纯 11 位 ID            | `原名.jpg` → `12345678901.jpg` |
| `id-prefix`  | 11 位 ID-原文件名      | `原名.jpg` → `12345678901-原名.jpg` |
| `strip-id`   | 去掉 `-` 后面的内容    | `12345678901-原名.jpg` → `12345678901.jpg` |
| `roundtrip`  | 加 ID → 导清单 → 还原  | 文件最终是**纯 11 位 ID**，另外留一份 ID↔原名的清单 |

三条必须保住的规矩（都是桌面工具踩过坑之后加的）：

1. **已经带 11 位数字前缀的文件不再叠第二个 ID**——否则重复跑一次就变成
   `新ID-旧ID-原名`，导出的清单直接作废。沿用下来的 ID 会**补记进台账**，
   免得以后发给别的图。
2. **`strip-id` 只在第一个 `-` 前面正好是 11 位纯数字时才裁**。
   原 `.bat` 是无条件截断，文件名里自带 `-` 的会被误伤。
3. 目标名已存在、或名字压根没变 → **跳过**（不算错）。

> 干跑预览是**照着内存里模拟出来的名字**报的，所以「预览看到的」和「真跑出来的」
> 一定一致。（桌面工具的干跑是先加前缀再刷新列表，预览里第二步会全部显示"不含 -"，
> 那是它预览的毛病，这里没跟着抄。）
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from temu_bot.config import Config
from temu_bot.toolbox import ids as ids_mod
from temu_bot.toolbox.files import list_images
from temu_bot.toolbox.result import FAILED, OK, SKIPPED, WOULD, ToolItem, ToolReport

log = logging.getLogger(__name__)

MODES = ("id-only", "id-prefix", "strip-id", "roundtrip")

MODE_LABELS: dict[str, str] = {
    "id-only": "1 - 纯 11 位 ID（丢弃原文件名）",
    "id-prefix": "2 - 11 位 ID-原文件名（加前缀）",
    "strip-id": "3 - 去掉 - 后面的内容（只保留 11 位 ID）",
    "roundtrip": "4 - 加 ID → 导对照清单 → 还原成纯 11 位 ID",
}

_PREFIX_MODES = ("id-prefix", "roundtrip")
_FINAL_MODES = ("id-only", "strip-id", "roundtrip")


class RenameError(RuntimeError):
    pass


def _core(
    cfg: Config | None,
    folder: Path,
    mode: str,
    *,
    id_length: int,
    csv_path: Path | None,
    export_format: str,
    dry_run: bool,
) -> ToolReport:
    if mode not in MODES:
        raise RenameError(f"不认识的重命名模式：{mode}（可选 {'/'.join(MODES)}）")

    taken = ids_mod.load_taken(cfg) if cfg is not None else set()
    items: list[ToolItem] = []
    new_ids: list[str] = []
    outputs: list[str] = []
    # 本轮已经"占"掉的文件名（小写）。同名文件互相撞车也要拦住，
    # 不能只看磁盘——磁盘上看不到这一轮刚改出来的名字。
    pending: set[str] = set()

    def free(target: Path) -> bool:
        key = target.name.lower()
        return key not in pending and not target.exists()

    def move(src: Path, dst: Path) -> None:
        if not dry_run:
            src.rename(dst)

    current = list_images(folder)
    if not current:
        return ToolReport(items=[], note=f"这个文件夹里没有图片：{folder}")

    # ---------------------------------------------------------- 第一遍：加 ID
    if mode in _PREFIX_MODES:
        after: list[Path] = []
        for p in current:
            hit = re.match(rf"^(\d{{{id_length}}})-", p.stem)
            if hit:
                existing = hit.group(1)
                if existing not in taken:
                    taken.add(existing)
                    new_ids.append(existing)
                items.append(
                    ToolItem(
                        p.name,
                        "加ID",
                        p.name,
                        SKIPPED,
                        f"已带 {id_length} 位 ID 前缀（{existing}），沿用原 ID，不叠加第二个",
                    )
                )
                after.append(p)
                continue

            new_id = ids_mod.new_unique_id(taken, id_length)
            dst = p.with_name(f"{new_id}-{p.stem}{p.suffix}")
            if not free(dst):
                items.append(ToolItem(p.name, "加ID", dst.name, SKIPPED, "目标名已存在，跳过"))
                after.append(p)
                continue
            pending.add(dst.name.lower())
            move(p, dst)
            items.append(ToolItem(p.name, "加ID", dst.name, OK if not dry_run else WOULD))
            new_ids.append(new_id)
            after.append(dst)
        current = after

        if mode == "roundtrip":
            # 这批发出去的 ID 先落台账，再导清单（万一后面出错，号也不会被重发）
            if not dry_run and cfg is not None:
                ids_mod.save_ids(cfg, new_ids)
                new_ids = []
            # 局部导入，避免循环
            from temu_bot.toolbox.listing import ListingError, _export_core

            try:
                report = _export_core(
                    folder, csv_path=csv_path, fmt=export_format, dry_run=dry_run
                )
            except ListingError as exc:
                # 比如两种清单格式都没勾 —— 加 ID 也做完一半了，得说清楚
                items.append(ToolItem("导出清单", "导出清单", "", FAILED, str(exc)))
                report = None
            if report is not None:
                items.extend(report.items)
                outputs.extend(report.outputs)

    # ------------------------------------------------- 第二遍：纯 ID / 还原 ID
    if mode in _FINAL_MODES:
        after = []
        for p in current:
            stem = p.stem
            if mode == "id-only":
                new_base = ids_mod.new_unique_id(taken, id_length)
                new_ids.append(new_base)
                action = "重命名"
            else:
                idx = stem.find("-")
                if idx > 0:
                    prefix = stem[:idx]
                    if len(prefix) == id_length and prefix.isdigit():
                        new_base = prefix
                        action = "还原ID"
                    else:
                        items.append(
                            ToolItem(
                                p.name,
                                "还原ID",
                                p.name,
                                SKIPPED,
                                f"前缀「{prefix}」不是 {id_length} 位数字，保持原样",
                            )
                        )
                        after.append(p)
                        continue
                else:
                    items.append(
                        ToolItem(p.name, "还原ID", p.name, SKIPPED, "文件名不含 - ，保持原样")
                    )
                    after.append(p)
                    continue

            new_name = f"{new_base}{p.suffix}"
            if new_name == p.name:
                items.append(ToolItem(p.name, "重命名", new_name, SKIPPED, "名称未变化"))
                after.append(p)
                continue
            dst = p.with_name(new_name)
            if not free(dst):
                items.append(ToolItem(p.name, "重命名", new_name, SKIPPED, "目标名已存在，跳过"))
                after.append(p)
                continue
            pending.add(dst.name.lower())
            move(p, dst)
            items.append(ToolItem(p.name, action, new_name, OK if not dry_run else WOULD))
            after.append(dst)
        current = after

    if not dry_run and cfg is not None:
        path = ids_mod.save_ids(cfg, new_ids)
        if path is not None:
            outputs.append(str(path))

    report = ToolReport(items=items, outputs=outputs)
    report.note = f"模式：{MODE_LABELS.get(mode, mode)}"
    return report


def plan(
    cfg: Config | None,
    folder: Path,
    mode: str = "id-only",
    *,
    id_length: int = ids_mod.DEFAULT_ID_LENGTH,
    csv_path: Path | None = None,
    export_format: str = "both",
) -> ToolReport:
    """干跑预览。**一个字节都不写。**"""
    try:
        return _core(
            cfg,
            folder,
            mode,
            id_length=id_length,
            csv_path=csv_path,
            export_format=export_format,
            dry_run=True,
        )
    except (NotADirectoryError, ids_mod.LedgerError) as exc:
        return ToolReport(items=[ToolItem(str(folder), "重命名", "", FAILED, str(exc))])


def run(
    cfg: Config,
    folder: Path,
    mode: str = "id-only",
    *,
    id_length: int = ids_mod.DEFAULT_ID_LENGTH,
    csv_path: Path | None = None,
    export_format: str = "both",
) -> ToolReport:
    """真改名。新发的 ID 落进台账。"""
    try:
        return _core(
            cfg,
            folder,
            mode,
            id_length=id_length,
            csv_path=csv_path,
            export_format=export_format,
            dry_run=False,
        )
    except (NotADirectoryError, ids_mod.LedgerError, OSError) as exc:
        log.exception("重命名失败")
        return ToolReport(items=[ToolItem(str(folder), "重命名", "", FAILED, str(exc))])
