"""工具箱「尺寸与格式 + 11 位 ID」一条龙的后台任务 + 进度。

为什么放后台线程：这条链路要逐张重写图片，几十张就要跑一会儿。同步请求
没法边跑边报进度，页面只能干等。这里起一个线程，页面轮询 `/status` 更新进度条。

同一时刻只允许一个任务（单用户、单文件夹，没必要并发）。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from temu_bot.config import Config

log = logging.getLogger(__name__)


class ComboBusy(RuntimeError):
    """已经有一条龙在跑。"""


@dataclass(slots=True)
class ComboRequest:
    folder: str
    width: int = 1340
    height: int = 1785
    target_format: str = "jpg"
    fit: str = "cover"
    quality: int = 95
    id_length: int = 11
    csv_path: str = ""
    export_format: str = "both"


class ComboRunner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

        self.state = "idle"          # idle / running / done / failed
        self.stage = ""              # resize / rename
        self.total = 0
        self.done = 0
        self.percent = 0
        self.message = ""
        self.error = ""
        self.result: dict[str, Any] | None = None
        self.folder = ""

    @property
    def busy(self) -> bool:
        return self.state == "running"

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self.state,
                "busy": self.state == "running",
                "stage": self.stage,
                "total": self.total,
                "done": self.done,
                "percent": self.percent,
                "message": self.message,
                "error": self.error,
                "result": self.result,
                "folder": self.folder,
            }

    def start(self, cfg: Config, request: ComboRequest) -> dict[str, Any]:
        folder = Path(request.folder)
        if not folder.is_dir():
            raise ValueError(f"文件夹不存在：{folder}")

        with self._lock:
            if self.state == "running":
                raise ComboBusy("已经有一条龙在跑了，等它结束再来")
            self.state = "running"
            self.stage = "resize"
            self.total = 0
            self.done = 0
            self.percent = 0
            self.message = "正在准备…"
            self.error = ""
            self.result = None
            self.folder = str(folder)

        self._thread = threading.Thread(
            target=self._run,
            args=(cfg, folder, request),
            name="temu-bot-combo",
            daemon=True,
        )
        self._thread.start()
        return self.snapshot()

    def _run(self, cfg: Config, folder: Path, request: ComboRequest) -> None:
        from temu_bot import toolbox

        def progress(index: int, total: int, src: Path) -> None:
            with self._lock:
                self.stage = "resize"
                self.total = total
                self.done = index
                self.percent = int(85 * index / total) if total else 85
                self.message = f"正在处理第 {index}/{total} 张：{src.name}"

        def stage(name: str) -> None:
            with self._lock:
                if name == "rename":
                    self.stage = "rename"
                    self.percent = 88
                    self.message = "尺寸与格式完成，正在生成 11 位 ID 与清单…"

        try:
            report = toolbox.combo.run(
                cfg,
                folder,
                width=request.width,
                height=request.height,
                target_format=request.target_format,
                fit=request.fit,
                quality=request.quality,
                id_length=request.id_length,
                csv_path=Path(request.csv_path) if request.csv_path else None,
                export_format=request.export_format,
                on_progress=progress,
                on_stage=stage,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("尺寸与格式 + 11 位 ID 一条龙失败")
            with self._lock:
                self.state = "failed"
                self.error = str(exc)
                self.message = f"失败：{exc}"
            return

        with self._lock:
            self.state = "done"
            self.stage = "done"
            self.percent = 100
            self.done = self.total
            self.result = {"folder": str(folder), **report.to_view()}
            self.message = report.summary()

    def wait(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
