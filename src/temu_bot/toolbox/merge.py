"""Excel 合并（对应桌面工具 `Core.ps1` 的 Invoke-ExcelMerge）。

把目录里所有工作簿的**所有工作表**纵向拼成一个 —— **不加表头、不去重、不做对齐**，
纯粹是"一行接一行"（跟原脚本一致）。

- 取 `.xls` + `.xlsx`（**只看这一层，不递归**），排除 `~$` 开头的 Excel 锁文件，
  排除**输出文件自身**（否则第二次跑会把上次的结果再合一遍）。
- **`.xls`（老格式）读不了**：openpyxl 不支持。会被列出来并记「跳过」，
  原因写明。桌面版靠 WPS/Excel 的 COM 才能读 `.xls`，那正是这套程序要摆脱的东西。
- 勾「每个工作簿前插入一行来源文件名」时，在该工作簿内容的**前面插一行**、
  只在 A 列放文件名（**每个工作簿一行，不是每个工作表一行**）。
- 默认输出 `<文件夹>\\MergedExcel.xlsx`。
- **写完回读校验**：读回来核对行数与内容非空。桌面版踩过"COM 不报错但什么都没写"
  的坑，宁可报错也不产出一张空表。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from openpyxl import Workbook, load_workbook

from temu_bot.toolbox.files import list_workbooks
from temu_bot.toolbox.result import FAILED, OK, SKIPPED, WOULD, ToolItem, ToolReport

log = logging.getLogger(__name__)

DEFAULT_NAME = "MergedExcel.xlsx"
SHEET_NAME = "Merged"
XLS_NOTE = "读不了 .xls 老格式（openpyxl 不支持）"


class MergeError(RuntimeError):
    pass


@dataclass(slots=True)
class Source:
    path: Path
    rows: list[list[object]]
    skipped: str = ""


def read_book(path: Path) -> list[list[object]]:
    """一个工作簿里所有工作表的行，按工作表顺序拼起来。

    每张表末尾那些"整行都空"的行会去掉（openpyxl 的 max_row 常被格式撑大）。
    """
    rows: list[list[object]] = []
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        for ws in wb.worksheets:
            sheet_rows = [list(r) for r in ws.iter_rows(values_only=True)]
            while sheet_rows and all(c is None or str(c).strip() == "" for c in sheet_rows[-1]):
                sheet_rows.pop()
            rows.extend(sheet_rows)
    finally:
        wb.close()
    return rows


def _read_sources(folder: Path, out_path: Path) -> list[Source]:
    sources: list[Source] = []
    for path in list_workbooks(folder, exclude=out_path):
        if path.suffix.lower() == ".xls":
            sources.append(Source(path=path, rows=[], skipped=XLS_NOTE))
            continue
        try:
            sources.append(Source(path=path, rows=read_book(path)))
        except Exception as exc:  # noqa: BLE001 —— 坏文件不该带崩整批
            sources.append(Source(path=path, rows=[], skipped=f"读不了：{exc}"))
    return sources


def _write_merged(out_path: Path, sources: list[Source], *, add_source_row: bool) -> int:
    rows_written = 0
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_NAME
    for source in sources:
        if source.skipped:
            continue
        if add_source_row:
            ws.append([source.path.name])
            rows_written += 1
        for row in source.rows:
            ws.append(row)
            rows_written += 1
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    wb.close()
    return rows_written


def _verify(out_path: Path, expected: int) -> int:
    check = load_workbook(out_path, read_only=True, data_only=True)
    try:
        actual = check[SHEET_NAME].max_row if SHEET_NAME in check.sheetnames else 0
    finally:
        check.close()
    if expected and actual != expected:
        raise MergeError(f"写完后校验失败：应有 {expected} 行，实际读到 {actual} 行")
    if expected and not actual:
        raise MergeError("写完后校验失败：目标表是空的，已中止以免产出一张空表")
    return actual


def _merge_core(
    folder: Path,
    *,
    output_file: Path | None = None,
    add_source_row: bool = False,
    dry_run: bool = True,
) -> ToolReport:
    out_path = Path(output_file) if output_file else folder / DEFAULT_NAME
    sources = _read_sources(folder, out_path)

    items: list[ToolItem] = []
    if not sources:
        return ToolReport(
            items=[ToolItem(str(folder), "合并Excel", str(out_path), SKIPPED, "目录里没有 .xls/.xlsx")],
            note="没有可合并的工作簿",
        )

    for source in sources:
        if source.skipped:
            items.append(ToolItem(source.path.name, "合并Excel", str(out_path), SKIPPED, source.skipped))
        elif dry_run:
            items.append(
                ToolItem(source.path.name, "合并Excel", str(out_path), WOULD, f"{len(source.rows)} 行")
            )

    if dry_run:
        return ToolReport(
            items=items,
            note=f"将合并 {sum(1 for s in sources if not s.skipped)} 个工作簿 → {out_path.name}",
        )

    usable = [s for s in sources if not s.skipped]
    if not usable:
        return ToolReport(items=items, note="没有能读的工作簿，什么都没写")

    try:
        expected = _write_merged(out_path, sources, add_source_row=add_source_row)
        actual = _verify(out_path, expected)
    except Exception as exc:  # noqa: BLE001 —— 写表/校验任何一步出错都别留下半张表
        log.exception("Excel 合并失败")
        items.append(ToolItem("合并Excel", "写 xlsx", str(out_path), FAILED, str(exc)))
        return ToolReport(items=items, note="合并失败")

    for source in usable:
        items.append(
            ToolItem(source.path.name, "合并Excel", str(out_path), OK, f"{len(source.rows)} 行")
        )
    return ToolReport(
        items=items,
        outputs=[str(out_path)],
        note=f"合了 {len(usable)} 个工作簿，共 {actual} 行 → {out_path.name}",
    )


def plan(
    folder: Path, *, output_file: Path | None = None, add_source_row: bool = False
) -> ToolReport:
    """干跑预览。**不写文件。**"""
    try:
        return _merge_core(folder, output_file=output_file, add_source_row=add_source_row, dry_run=True)
    except NotADirectoryError as exc:
        return ToolReport(items=[ToolItem(str(folder), "合并Excel", "", FAILED, str(exc))])


def run(
    folder: Path, *, output_file: Path | None = None, add_source_row: bool = False
) -> ToolReport:
    """真合并。"""
    try:
        return _merge_core(
            folder, output_file=output_file, add_source_row=add_source_row, dry_run=False
        )
    except (NotADirectoryError, OSError) as exc:
        log.exception("Excel 合并失败")
        return ToolReport(items=[ToolItem(str(folder), "合并Excel", "", FAILED, str(exc))])
