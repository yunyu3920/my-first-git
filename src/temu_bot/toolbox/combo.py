"""尺寸与格式 + 11 位 ID：一条龙处理（用户 2026-10-18 要求合并）。

顺序固定：

1. **尺寸与格式**：把每张图改成 1340×1785 / JPG，**覆盖原图、保持文件名**
   （png/webp 会转成同名 jpg 并删掉原文件）；
2. **11 位 ID - 导表 - 保留 11 位 ID**：加 ID → 导出清单 → 还原成纯 11 位 ID。

合并后的结果里：

- **明细** = 两步的逐项结果拼在一起；
- **产出** = **只保留第二步的清单/台账**，不再显示尺寸与格式那堆「产出=原图路径」；
- 进度回调只覆盖最耗时的第一步（逐张处理），第二步切换成一个阶段提示。
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from temu_bot.config import Config
from temu_bot.toolbox import rename, resize
from temu_bot.toolbox.result import ToolReport


def _merge(first: ToolReport, second: ToolReport) -> ToolReport:
    notes = [n for n in (first.note, second.note) if n]
    return ToolReport(
        items=list(first.items) + list(second.items),
        # **只显示第二步的产出**：尺寸与格式的 outputs 是原图路径，没什么可看的
        outputs=list(second.outputs),
        note="；".join(notes),
    )


def plan(
    cfg: Config | None,
    folder: Path,
    *,
    width: int = resize.DEFAULT_WIDTH,
    height: int = resize.DEFAULT_HEIGHT,
    target_format: str = resize.DEFAULT_FORMAT,
    fit: str = "cover",
    quality: int = resize.DEFAULT_QUALITY,
    id_length: int = 11,
    csv_path: Path | None = None,
    export_format: str = "both",
) -> ToolReport:
    """干跑预览：尺寸与格式只探测，ID 步骤也只模拟名字，一个字节都不写。"""
    first = resize.plan(
        folder,
        width=width,
        height=height,
        target_format=target_format,
        fit=fit,
        quality=quality,
        inplace=True,
        # 不逐张打开图片：大文件夹预览也能很快返回（执行时仍会逐张处理并报错）
        probe=False,
    )
    second = rename.plan(
        cfg,
        folder,
        "roundtrip",
        id_length=id_length,
        csv_path=csv_path,
        export_format=export_format,
    )
    return _merge(first, second)


def run(
    cfg: Config | None,
    folder: Path,
    *,
    width: int = resize.DEFAULT_WIDTH,
    height: int = resize.DEFAULT_HEIGHT,
    target_format: str = resize.DEFAULT_FORMAT,
    fit: str = "cover",
    quality: int = resize.DEFAULT_QUALITY,
    id_length: int = 11,
    csv_path: Path | None = None,
    export_format: str = "both",
    on_progress: Callable[[int, int, Path], None] | None = None,
    on_stage: Callable[[str], None] | None = None,
) -> ToolReport:
    """真执行：先尺寸与格式（覆盖原图），再 11 位 ID + 导清单 + 还原。"""
    first = resize.run(
        folder,
        width=width,
        height=height,
        target_format=target_format,
        fit=fit,
        quality=quality,
        inplace=True,
        on_progress=on_progress,
    )
    if on_stage is not None:
        try:
            on_stage("rename")
        except Exception:  # noqa: BLE001 - 进度回调是旁路
            pass
    second = rename.run(
        cfg,
        folder,
        "roundtrip",
        id_length=id_length,
        csv_path=csv_path,
        export_format=export_format,
    )
    return _merge(first, second)
