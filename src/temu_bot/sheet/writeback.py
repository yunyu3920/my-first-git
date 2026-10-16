"""上架完把「商品ID / 状态 / 问题项」写回商品表。

**为什么必须在流程里做**：表格里没有商品ID 的行，在流水线眼里就是"新建"。
同一行再跑一次会**再复制一遍模板、再建一个重复商品**——重复铺货是会被平台处罚的。
所以回写不是"顺手记一笔"，它是**防重复铺货的闸门**：跑一行就锁一行。

拆成两层：
- `updates_for()` **纯函数**：算这一行该写哪几个格子（好测，不碰磁盘）；
- `write_back()` 有副作用：走 `guard.write_updates`（锁 + 备份 + 原子替换）。

写表可能失败（表开着 Excel、被别的进程锁住）。**回写失败不该让上架结果作废**——
商品已经建好了，抹掉只会更糟。所以这里把失败包成 `WritebackReport(ok=False, ...)`，
由上层决定怎么提示。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from temu_bot.config import Config, SheetCfg
from temu_bot.sheet.guard import CellUpdate, TableBusyError, write_updates
from temu_bot.sheet.schema import COL_PROBLEM, COL_PRODUCT_ID, COL_STATUS, SheetRow

log = logging.getLogger(__name__)

# 问题项写进表格时的长度上限：这一列是给人看的，别把整段日志塞进去
PROBLEM_MAX = 200


@dataclass(slots=True)
class WritebackReport:
    """回写结果。`ok=False` 时 `message` 说明为什么没写成。"""

    row_index: int
    ok: bool
    message: str
    written: dict[int, str] = field(default_factory=dict)
    backup: Path | None = None


def statuses_of(cfg: Config | None, sheet: SheetCfg | None = None) -> dict[str, str]:
    """取状态文案（`成功`/`失败`…），配置缺项时用默认值兜住。"""
    table = (sheet or (cfg.sheet if cfg is not None else None))
    configured = dict(table.statuses) if table is not None else {}
    return {
        "success": configured.get("success", "成功"),
        "failed": configured.get("failed", "失败"),
        "need_human": configured.get("need_human", "需人工"),
    }


def first_problem(failed: bool, steps: list) -> str:
    """第一条 FAIL 步骤的说明——写进「问题项」列，省得再去翻日志。"""
    if not failed:
        return ""
    for step in steps:
        status = getattr(step, "status", None)
        if getattr(status, "value", status) != "fail":
            continue
        name = getattr(step, "name", "?")
        detail = str(getattr(step, "detail", "") or "").strip()
        text = f"[{name}] {detail}" if detail else f"[{name}]"
        return text if len(text) <= PROBLEM_MAX else text[: PROBLEM_MAX - 1] + "…"
    return ""


def updates_for(
    row: SheetRow,
    *,
    product_id: str,
    failed: bool,
    steps: list,
    statuses: dict[str, str],
) -> CellUpdate:
    """算出这一行该写哪几个格子（**纯函数**，不碰磁盘）。

    - 有商品ID 才写第 13 列——没有就留空，绝不拿别的东西顶上；
    - 状态按成功/失败写第 14 列；
    - 问题项第 16 列：失败时写第一条失败原因，成功时**清空**（旧的失败痕迹要抹掉）。
    """
    fields: dict[int, object] = {COL_STATUS: statuses["failed"] if failed else statuses["success"]}
    if product_id:
        fields[COL_PRODUCT_ID] = product_id
    fields[COL_PROBLEM] = first_problem(failed, steps)
    return CellUpdate(row_index=row.row_index, fields=fields)


def write_back(
    cfg: Config,
    update: CellUpdate,
    *,
    backup_first: bool = True,
) -> WritebackReport:
    """真写。表被占用/被锁 → 返回 `ok=False` 的报告，**不抛异常**（上架结果要保住）。"""
    try:
        backup_path = write_updates(cfg, [update], backup_first=backup_first)
    except TableBusyError as exc:
        log.warning("商品ID/状态没能写回表格（第 %s 行）：%s", update.row_index, exc)
        return WritebackReport(
            row_index=update.row_index,
            ok=False,
            message=f"表格被占用，没写回：{exc}",
        )
    except (PermissionError, OSError, KeyError, ValueError) as exc:
        log.warning("商品ID/状态没能写回表格（第 %s 行）：%s", update.row_index, exc)
        return WritebackReport(
            row_index=update.row_index,
            ok=False,
            message=f"写回失败：{exc}",
        )

    written = {col: str(value) for col, value in update.fields.items() if value != ""}
    detail = "、".join(f"第 {col} 列={value}" for col, value in sorted(written.items()))
    log.info("已写回第 %s 行：%s", update.row_index, detail)
    return WritebackReport(
        row_index=update.row_index,
        ok=True,
        message=detail or "没有要写的内容",
        written={str(col): str(value) for col, value in written.items()},
        backup=backup_path,
    )
