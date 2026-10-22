"""新用户的表只有一张、但工作表名不叫 Sheet1 时，自动用唯一那张。"""

from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest

from temu_bot.sheet.schema import EXPECTED_HEADER
from temu_bot.sheet.table import SheetTable


def make_book(path: Path, titles: list[str]) -> Path:
    wb = openpyxl.Workbook()
    first = wb.active
    first.title = titles[0]
    first.append([EXPECTED_HEADER[i] for i in sorted(EXPECTED_HEADER)])
    for title in titles[1:]:
        wb.create_sheet(title)
    wb.save(path)
    return path


def test_single_sheet_with_a_different_name_is_used(tmp_path: Path):
    path = make_book(tmp_path / "商品信息表.xlsx", ["商品信息表"])

    table = SheetTable(path, "Sheet1")

    assert table.read_headers(), "应该能读到表头"
    assert table.sheet_name == "商品信息表"


def test_multiple_sheets_still_require_the_configured_name(tmp_path: Path):
    path = make_book(tmp_path / "商品信息表.xlsx", ["商品信息表", "另一个"])

    table = SheetTable(path, "Sheet1")

    with pytest.raises(KeyError) as exc:
        table.read_headers()
    assert "实际有" in str(exc.value), "多张表时必须让人去设置里选，不能猜"
