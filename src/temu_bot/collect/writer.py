"""把扫描结果写成一张新的「商品信息表」。

**绝不覆盖已有文件。** 素材目录里本来就可能躺着一张同名的表
（用户的 `C:\\Users\\A\\Desktop\\Temu\\商品信息表.xlsx` 就是），
一声不吭覆盖掉是最糟糕的事故。所以目标文件已存在时自动加时间戳另存，
并把最终路径如实报出来。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import openpyxl

from temu_bot.collect.scan import CollectOptions, ScanResult, generate_material, row_cells
from temu_bot.sheet import schema

log = logging.getLogger(__name__)

DEFAULT_NAME = "商品信息表.xlsx"


@dataclass(slots=True)
class WriteResult:
    path: Path
    rows: int
    generated_materials: list[str] = field(default_factory=list)
    renamed: bool = False
    original_target: Path | None = None

    def summary(self) -> str:
        parts = [f"已写出 {self.rows} 行 → {self.path}"]
        if self.renamed and self.original_target is not None:
            parts.append(f"（原目标 {self.original_target.name} 已存在，另存为新文件，没动原文件）")
        if self.generated_materials:
            parts.append(f"生成了 {len(self.generated_materials)} 张素材图")
        return "；".join(parts)


def default_output(folder: Path) -> Path:
    return Path(folder) / DEFAULT_NAME


def safe_output_path(target: Path) -> tuple[Path, bool]:
    """目标已存在就另存（加时间戳），返回 (实际路径, 是否改名)。"""
    target = Path(target)
    if not target.exists():
        return target, False
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return target.with_name(f"{target.stem}-{stamp}{target.suffix}"), True


def header_row() -> list[str]:
    """按列号排好的表头（与 sheet/schema.py 的契约一致）。"""
    return [schema.EXPECTED_HEADER[i] for i in sorted(schema.EXPECTED_HEADER)]


def write_table(
    result: ScanResult,
    target: Path | None = None,
    *,
    generate_missing_material: bool = False,
) -> WriteResult:
    """写出表格。`generate_missing_material=True` 时才真的生成素材图。"""
    options: CollectOptions = result.options
    folder = Path(options.folder)

    if not result.rows:
        raise ValueError("没有可写出的行（一张图都没收到）")

    target = Path(target) if target else default_output(folder)
    final, renamed = safe_output_path(target)

    generated: list[str] = []
    if generate_missing_material:
        for row in result.rows:
            if not row.generated_material:
                continue
            dst = folder / row.material_name
            if dst.exists():
                continue
            if generate_material(folder / row.main_name, dst, options.material_size):
                generated.append(dst.name)
            else:
                # 生成失败就别写进表里，免得表里指着一张不存在的图
                row.material_name = ""
                row.note = "素材图生成失败"

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    sheet.append(header_row())

    columns = sorted(schema.EXPECTED_HEADER)
    for row in result.rows:
        cells = row_cells(row, options)
        sheet.append([cells.get(col, "") for col in columns])

    final.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(final)
    workbook.close()

    log.info("收集完成：%d 行 → %s", len(result.rows), final)
    return WriteResult(
        path=final,
        rows=len(result.rows),
        generated_materials=generated,
        renamed=renamed,
        original_target=target if renamed else None,
    )
