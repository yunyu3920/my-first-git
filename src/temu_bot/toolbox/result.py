"""工具箱的"一行结果" —— 桌面那个工具箱管它叫 Item/Action/Target/Status/Note。

字段名和状态取值都照着它来，网页上那张结果表的配色就能直接沿用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 状态取值（对应桌面工具的颜色语义）
WOULD = "would"  # 蓝色：干跑，届时会这么做
OK = "ok"  # 绿色：做成了
SKIPPED = "skipped"  # 灰色：按规矩跳过
FAILED = "failed"  # 红色：出错

# 给网页用的中文说明（结果表里当状态徽章的文字）
STATUS_LABELS: dict[str, str] = {
    WOULD: "待执行",
    OK: "成功",
    SKIPPED: "跳过",
    FAILED: "失败",
}


@dataclass(slots=True)
class ToolItem:
    """一个文件（或一个输出）的处理结果。

    `name`   处理的源文件名（导出/合并这类没有源文件的，写动作名）
    `action` 干了什么（加ID / 还原ID / 转 png / 导出 CSV / 合并Excel…）
    `target` 目标路径或新名字
    `status` WOULD / OK / SKIPPED / FAILED
    `detail` 给人看的说明（跳过原因、尺寸、错误信息…）
    """

    name: str
    action: str
    target: str = ""
    status: str = WOULD
    detail: str = ""

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    def to_view(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "action": self.action,
            "target": self.target,
            "status": self.status,
            "status_label": STATUS_LABELS.get(self.status, self.status),
            "detail": self.detail,
        }


@dataclass(slots=True)
class ToolReport:
    """一次操作的完整结果：逐项明细 + 一句话总结。"""

    items: list[ToolItem] = field(default_factory=list)
    note: str = ""
    outputs: list[str] = field(default_factory=list)  # 这次产出了哪些文件

    @property
    def counts(self) -> dict[str, int]:
        out = {WOULD: 0, OK: 0, SKIPPED: 0, FAILED: 0}
        for item in self.items:
            out[item.status] = out.get(item.status, 0) + 1
        return out

    @property
    def ok(self) -> bool:
        """没有 FAILED 就算成。跳过不算失败。"""
        return not any(i.status == FAILED for i in self.items)

    def summary(self) -> str:
        c = self.counts
        parts = [f"{v} {STATUS_LABELS[k]}" for k, v in c.items() if v]
        head = "；".join(parts) or "没有要处理的东西"
        return f"{head}{('。' + self.note) if self.note else ''}"

    def to_view(self) -> dict[str, Any]:
        return {
            "items": [i.to_view() for i in self.items],
            "note": self.note,
            "outputs": list(self.outputs),
            "counts": self.counts,
            "ok": self.ok,
            "summary": self.summary(),
        }
