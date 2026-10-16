from __future__ import annotations

import pytest

from temu_bot.sheet import guard
from temu_bot.sheet.schema import COL_PROBLEM, COL_PRODUCT_ID, COL_STATUS
from temu_bot.sheet.table import SheetTable


def test_read_rows_from_sandbox(sandbox):
    cfg = sandbox["cfg"]
    rows = list(SheetTable.from_config(cfg).iter_rows())
    assert len(rows) == 2                       # 第三行没主图，被过滤
    assert rows[0].main_image == "00742093597.jpg"
    assert rows[0].material_image == "00742093597-素材图.jpg"
    assert rows[0].template_spu == "8909735440"
    assert rows[0].status == "成功"
    assert rows[1].status == "失败"


def test_skip_done_and_only_status_filters(sandbox):
    cfg = sandbox["cfg"]
    st = SheetTable.from_config(cfg)
    assert [r.row_index for r in st.iter_rows(skip_done=True)] == [3]
    assert [r.row_index for r in st.iter_rows(only_status="失败")] == [3]
    assert [r.row_index for r in st.iter_rows(only_shop="A lovely costume")] == [2, 3]


def test_busy_detection_via_lock_file(sandbox):
    table = sandbox["table"]
    lock = table.parent / f"~${table.name}"
    lock.write_text("", encoding="utf-8")
    try:
        with pytest.raises(guard.TableBusyError) as exc:
            guard.assert_not_busy(table)
        assert "锁文件" in str(exc.value)
    finally:
        lock.unlink()
    guard.assert_not_busy(table)                # 去掉锁后应当通过


def test_write_updates_backs_up_and_writes(sandbox):
    cfg = sandbox["cfg"]
    table = sandbox["table"]

    backup_path = guard.write_updates(
        cfg,
        [guard.CellUpdate(row_index=2, fields={COL_STATUS: "处理中", COL_PROBLEM: "测试写入"})],
        backup_first=True,
    )
    assert backup_path is not None and backup_path.exists()
    assert backup_path.parent == cfg.data_dir / "backups"

    rows = {r.row_index: r for r in SheetTable.from_config(cfg).iter_rows()}
    assert rows[2].status == "处理中"
    assert rows[2].problem == "测试写入"
    assert rows[3].status == "失败"              # 别的行没被动
    assert rows[2].product_id == "184807703165778961"

    # 原表里不该残留临时文件
    assert not list(table.parent.glob("*.tmp.xlsx"))


def test_write_updates_can_clear_a_cell(sandbox):
    """`value=None` 必须真的把格子清空。

    踩过的坑：原实现写的是 `ws.cell(row=..., column=..., value=None)`，而 openpyxl 的
    `Worksheet.cell()` 在 `value is None` 时**直接跳过赋值** → "清空"静默变成什么都不做。
    文件照样被重存（体积还变了），看着像成功，值其实还在。
    """
    cfg = sandbox["cfg"]
    table = sandbox["table"]

    guard.write_updates(
        cfg,
        [guard.CellUpdate(row_index=2, fields={COL_PRODUCT_ID: None, COL_STATUS: None})],
    )

    rows = {r.row_index: r for r in SheetTable.from_config(cfg).iter_rows()}
    assert rows[2].product_id == "", "第 13 列没被清掉"
    assert rows[2].status == ""
    assert rows[3].product_id == "184807703165779045", "别的行不该被动"


def test_write_updates_clears_with_an_empty_string_too(sandbox):
    """空字符串也要能覆盖掉原来的值（回写"问题项"用的就是它）。"""
    cfg = sandbox["cfg"]
    guard.write_updates(cfg, [guard.CellUpdate(row_index=3, fields={COL_PROBLEM: ""})])

    rows = {r.row_index: r for r in SheetTable.from_config(cfg).iter_rows()}
    assert rows[3].problem == ""


def test_write_updates_refuses_header_rows(sandbox):
    cfg = sandbox["cfg"]
    with pytest.raises(ValueError):
        guard.write_updates(cfg, [guard.CellUpdate(row_index=1, fields={COL_STATUS: "x"})])


def test_write_updates_noop_when_empty(sandbox):
    cfg = sandbox["cfg"]
    assert guard.write_updates(cfg, [guard.CellUpdate(row_index=2, fields={})]) is None


def test_backup_prunes_old_files(sandbox):
    cfg = sandbox["cfg"]
    table = sandbox["table"]
    backup_dir = cfg.data_dir / "backups"
    for i in range(5):
        stale = backup_dir / f"{table.stem}.2026010{i}-000000.bak{table.suffix}"
        stale.write_text(str(i), encoding="utf-8")
    guard.backup(table, backup_dir, keep=2)
    kept = sorted(p.name for p in backup_dir.glob("*.bak.xlsx"))
    assert len(kept) == 2
