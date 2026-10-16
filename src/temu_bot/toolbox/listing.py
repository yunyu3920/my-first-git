"""导出图片清单（对应桌面工具 `Core.ps1` 的 Invoke-ExportImageList）。

每个文件按文件名**第一个 `-`**拆成「数字前缀 / 中文后缀」两列，输出两份：

1. **`图片文件名清单.csv`** —— 与老 `.bat` 脚本**逐字节一致**，下游流程不用改：
   - **GBK（代码页 936）、无 BOM、CRLF**
   - 表头 `"数字前缀","中文后缀" ` —— **行尾有一个空格**
   - 数据行 `"前缀","后缀" ` —— 同样行尾一个空格
   - 那个空格是原来 `cmd` 的 `echo ... >> 文件` 把重定向前的空格也写进去了，
     **属于原样复刻**，不是笔误，别"顺手修掉"
   - 文件名里有 GBK 表示不了的字符（emoji）→ 写成 `?` 并继续，不中断
   - 写完**回读核对行数**，不符就报错
2. **`图片文件名清单.xlsx`** —— 工作表名 `图片文件名清单`，A 列数字前缀 / B 列中文后缀，
   表头加粗、冻结首行、带自动筛选、A 列窄 B 列宽。

**同名文件不会被覆盖**：目标已存在就先把旧的改名成 `<原名>.bak-<时间戳>`——
那份清单是原文件名的唯一记录，覆盖掉就找不回来了。
旧清单正被 Excel/WPS 打开时（改名失败）→ 这个目标记失败并跳过，另一个照常，
整个导出不崩。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font

from temu_bot.toolbox.files import list_images
from temu_bot.toolbox.result import FAILED, OK, WOULD, ToolItem, ToolReport

log = logging.getLogger(__name__)

CSV_NAME = "图片文件名清单.csv"
XLSX_SHEET = "图片文件名清单"
CSV_HEADER = '"数字前缀","中文后缀" '
FORMATS = ("both", "csv", "xlsx")
NO_FORMAT = "请至少勾选一种输出格式（CSV 或 XLSX）。"

# 与桌面工具一致：A 列窄（11 位 ID）、B 列宽（中文名很长）
COL_WIDTHS = {"A": 14, "B": 80}


class ListingError(RuntimeError):
    pass


@dataclass(slots=True)
class Entry:
    prefix: str
    suffix: str


def split_name(stem: str) -> Entry:
    """按**第一个** `-` 拆；`-` 在开头或没有 `-` 时，整个名字当前缀。

    后面还有 `-` 的话，留在后缀里。
    """
    idx = stem.find("-")
    if idx > 0:
        return Entry(stem[:idx], stem[idx + 1 :])
    return Entry(stem, "")


def entries_of(folder: Path) -> list[Entry]:
    return [split_name(p.stem) for p in list_images(folder)]


def _csv_lines(entries: list[Entry]) -> list[str]:
    lines = [CSV_HEADER]
    for e in entries:
        lines.append('"{0}","{1}" '.format(e.prefix.replace('"', '""'), e.suffix.replace('"', '""')))
    return lines


def write_csv(path: Path, entries: list[Entry]) -> None:
    """写 CSV，然后**回读核对行数**。"""
    lines = _csv_lines(entries)
    # GBK + errors=replace：GBK 表示不了的字符（emoji）写成 '?' 继续，不中断
    payload = "".join(line + "\r\n" for line in lines).encode("gbk", errors="replace")
    path.write_bytes(payload)

    back = path.read_bytes().decode("gbk", errors="replace").splitlines()
    if len(back) != len(entries) + 1:
        raise ListingError(f"写完后校验失败：应有 {len(entries) + 1} 行，实际 {len(back)} 行")


def write_xlsx(path: Path, entries: list[Entry]) -> None:
    """写 xlsx，然后**回读核对行数**。"""
    wb = Workbook()
    ws = wb.active
    ws.title = XLSX_SHEET
    ws.append(["数字前缀", "中文后缀"])
    for e in entries:
        ws.append([e.prefix, e.suffix])

    for cell in ("A1", "B1"):
        ws[cell].font = Font(bold=True)
    ws.freeze_panes = "A2"
    last = len(entries) + 1
    if last >= 2:
        ws.auto_filter.ref = f"A1:B{last}"
    for col, width in COL_WIDTHS.items():
        ws.column_dimensions[col].width = width

    wb.save(path)
    wb.close()

    check = load_workbook(path, read_only=True)
    try:
        actual = check[XLSX_SHEET].max_row
    finally:
        check.close()
    if actual != last:
        raise ListingError(f"写完后校验失败：应有 {last} 行，实际读到 {actual} 行")


def _backup(path: Path) -> Path:
    """旧清单改名保留。名字带时间戳，一秒内连跑两次也不会互相盖掉。"""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = path.with_name(f"{path.name}.bak-{stamp}")
    path.rename(bak)
    return bak


def _export_core(
    folder: Path,
    *,
    csv_path: Path | None = None,
    fmt: str = "both",
    dry_run: bool = True,
) -> ToolReport:
    if fmt not in FORMATS:
        raise ListingError(NO_FORMAT)

    csv_path = Path(csv_path) if csv_path else folder / CSV_NAME
    xlsx_path = csv_path.with_suffix(".xlsx")
    targets: list[tuple[str, Path]] = []
    if fmt != "xlsx":
        targets.append(("CSV", csv_path))
    if fmt != "csv":
        targets.append(("XLSX", xlsx_path))

    entries = entries_of(folder)
    items: list[ToolItem] = []
    outputs: list[str] = []

    if dry_run:
        for kind, path in targets:
            note = f"{len(entries)} 行"
            if path.exists():
                note += "；目标已存在，执行时会先备份成 .bak-时间戳"
            items.append(ToolItem("导出清单", f"导出 {kind}", str(path), WOULD, note))
        return ToolReport(items=items, outputs=outputs)

    # 先备份旧清单。被 Excel/WPS 占着时改名会失败——这种要给人话提示并跳过，
    # 不能把整个导出带崩（用户很可能正开着上次的清单在看）。
    blocked: set[Path] = set()
    for _kind, path in targets:
        if not path.exists():
            continue
        try:
            bak = _backup(path)
            items.append(
                ToolItem(path.name, "备份旧清单", str(bak), OK, "同名文件已存在，先改名保留")
            )
        except OSError as exc:
            blocked.add(path)
            items.append(
                ToolItem(
                    path.name,
                    "备份旧清单",
                    str(path),
                    FAILED,
                    f"旧文件正被占用、无法改名（是不是开着 Excel/WPS？先关掉再试）：{exc}。"
                    "本次跳过这个文件。",
                )
            )

    for kind, path in targets:
        if path in blocked:
            continue
        try:
            if kind == "CSV":
                write_csv(path, entries)
            else:
                write_xlsx(path, entries)
        except (ListingError, OSError) as exc:
            items.append(ToolItem("导出清单", f"导出 {kind}", str(path), FAILED, str(exc)))
            continue
        items.append(ToolItem("导出清单", f"导出 {kind}", str(path), OK, f"{len(entries)} 行"))
        outputs.append(str(path))

    return ToolReport(items=items, outputs=outputs)


def plan(folder: Path, *, csv_path: Path | None = None, fmt: str = "both") -> ToolReport:
    """干跑预览。**不写任何文件。**"""
    try:
        return _export_core(folder, csv_path=csv_path, fmt=fmt, dry_run=True)
    except (NotADirectoryError, ListingError) as exc:
        return ToolReport(items=[ToolItem(str(folder), "导出清单", "", FAILED, str(exc))])


def run(folder: Path, *, csv_path: Path | None = None, fmt: str = "both") -> ToolReport:
    """真导出。"""
    try:
        return _export_core(folder, csv_path=csv_path, fmt=fmt, dry_run=False)
    except (NotADirectoryError, ListingError, OSError) as exc:
        log.exception("导出清单失败")
        return ToolReport(items=[ToolItem(str(folder), "导出清单", "", FAILED, str(exc))])


__all__ = [
    "CSV_HEADER",
    "CSV_NAME",
    "Entry",
    "ListingError",
    "NO_FORMAT",
    "XLSX_SHEET",
    "entries_of",
    "plan",
    "run",
    "split_name",
    "write_csv",
    "write_xlsx",
]
