"""控制台的任务运行器：把上架主流程放到后台线程跑，并把进度暴露给网页。

**关于「取消」的诚实说明**：`ListingPipeline.run()` 是同步阻塞的，一行跑到一半
**没法安全中断**——断在 COPY 之后、APPLY 之前会留下一个"复制了但没改标题"的商品。
所以取消只在**行与行之间**生效：请求取消后，当前这一行跑完就停。
接口回的话术也要照这个写，不能假装按一下就能立刻停。
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from temu_bot.config import Config
from temu_bot.dianxiaomi.client import OP_PUBLISH, DxmClient, DxmError
from temu_bot.listing import ListingOutcome, ListingPipeline, Status, StepResult
from temu_bot.sheet.schema import SheetRow
from temu_bot.sheet.table import SheetTable

log = logging.getLogger(__name__)


class JobState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BusyError(RuntimeError):
    """已经有任务在跑了——同时只允许一个（单账号 + 限速，本就不该并发）。"""


@dataclass(slots=True)
class RunRequest:
    """一次运行请求。默认干跑，真实执行必须显式 live=True。"""

    # 空的 = 不指定登录身份，**由流水线按表格里的店铺名自动认**（用户 2026-10-16 要求）
    shop: str = ""
    row_indexes: list[int] = field(default_factory=list)
    images_dir: Path | None = None
    live: bool = False
    op: int = 1
    clean_images: bool = False
    # None = 按行自动判断：表里已有商品ID 的行（重试/续跑）不重复传图；新行才传。
    swap_images: bool | None = None


@dataclass(slots=True)
class RowResult:
    row_index: int
    outcome: ListingOutcome | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome is not None and not self.outcome.failed

    def as_dict(self) -> dict[str, Any]:
        if self.outcome is None:
            return {"row_index": self.row_index, "ok": False, "error": self.error, "steps": []}
        payload = self.outcome.as_dict()
        payload["row_index"] = self.row_index
        payload["ok"] = self.ok
        return payload


class JobRunner:
    """跑一批行的任务。同一时刻只允许一个。"""

    def __init__(
        self,
        *,
        row_loader: Callable[[int], SheetRow | None] | None = None,
        pipeline_factory: Callable[..., ListingPipeline] | None = None,
        on_clean: Callable[[Config, str], None] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()

        self.state: JobState = JobState.IDLE
        self.request: RunRequest | None = None
        self.steps: list[dict[str, Any]] = []
        self.results: list[RowResult] = []
        self.current_row: int | None = None
        self.error: str = ""
        self.cancelled_requested = False
        # 有行疑似遇到店小秘接口变更时，写一份诊断报告（给 AI/人排障用）
        self.site_change_report: str = ""

        # 可注入，便于测试时不碰真表、不碰真接口
        self._row_loader = row_loader
        self._pipeline_factory = pipeline_factory
        self._on_clean = on_clean

    # ---------------------------------------------------------- 状态
    @property
    def busy(self) -> bool:
        return self.state is JobState.RUNNING

    def snapshot(self) -> dict[str, Any]:
        results = [r.as_dict() for r in self.results]
        # 回写失败要单独点出来：上架是成功的（商品已建好），但表格没记上，
        # 不补写的话下次跑这一行就是重复铺货
        writeback_failed = [r["row_index"] for r in results if r.get("writeback_ok") is False]
        return {
            "state": self.state.value,
            "busy": self.busy,
            "current_row": self.current_row,
            "cancel_requested": self.cancelled_requested,
            "error": self.error,
            "live": self.request.live if self.request else None,
            "rows": list(self.request.row_indexes) if self.request else [],
            "total": len(self.request.row_indexes) if self.request else 0,
            "finished": len(self.results),
            "steps": list(self.steps),
            "results": results,
            "writeback_failed": writeback_failed,
            "site_change_report": self.site_change_report,
            "site_changed_rows": self._site_changed_rows(),
        }

    # ---------------------------------------------------------- 起任务
    def start(self, cfg: Config, request: RunRequest) -> None:
        with self._lock:
            if self.busy:
                raise BusyError("已经有任务在跑了，等它结束再来")
            if not request.row_indexes:
                raise BusyError("没选要跑的行")

            self.state = JobState.RUNNING
            self.request = request
            self.steps = []
            self.results = []
            self.current_row = None
            self.error = ""
            self.cancelled_requested = False
            self.site_change_report = ""
            self._cancel.clear()

        self._thread = threading.Thread(
            target=self._run, args=(cfg, request), name="temu-bot-job", daemon=True
        )
        self._thread.start()

    def request_cancel(self) -> str:
        """请求取消。**当前这一行不会被中断**，跑完才停。"""
        if not self.busy:
            return "当前没有在跑的任务"
        self._cancel.set()
        self.cancelled_requested = True
        log.warning("已请求取消：当前这一行会跑完，之后不再继续")
        return "已请求取消：当前这一行会跑完，之后不再开始新的行"

    # ---------------------------------------------------------- 跑
    def _run(self, cfg: Config, request: RunRequest) -> None:
        try:
            rows = self._load_rows(cfg, request)
            for row in rows:
                if self._cancel.is_set():
                    self.state = JobState.CANCELLED
                    log.warning("已取消，剩余行未跑")
                    return
                self.current_row = row.row_index
                self._run_one(cfg, request, row)
        except Exception as exc:  # noqa: BLE001
            self.state = JobState.FAILED
            self.error = str(exc)
            log.exception("任务失败")
            return

        try:
            self._verify_pending(cfg, request, rows)
        except Exception:  # noqa: BLE001
            # 复核只是"把表格写得更准"，它自己挂了不能把整批结果带崩
            log.exception("批次结束复核发布状态失败（不影响已提交的商品）")

        self._write_site_change_report(cfg, request)
        self.state = JobState.CANCELLED if self._cancel.is_set() else JobState.DONE
        self.current_row = None

    def _load_rows(self, cfg: Config, request: RunRequest) -> list[SheetRow]:
        if self._row_loader is not None:
            loaded = [self._row_loader(i) for i in request.row_indexes]
            return [r for r in loaded if r is not None]

        table = SheetTable.from_config(cfg)
        wanted = set(request.row_indexes)
        rows = [row for row in table.iter_rows() if row.row_index in wanted]

        # 选中的行里，有的可能在页面加载之后被清空/删掉了。这类行读表时就被跳过，
        # **不报错、不打断整批**——这里只把"你选了但没跑"的行号说清楚，
        # 免得结果里少了一行还找不到原因。
        missing = sorted(wanted - {row.row_index for row in rows})
        if missing:
            log.warning(
                "第 %s 行不是有效数据行（全空，或缺「文件夹路径」/「主图」），已跳过；"
                "其余行照常跑",
                "、".join(str(i) for i in missing),
            )
        return rows

    def _record_step(self, row_index: int, step: StepResult) -> None:
        self.steps.append(
            {
                "row_index": row_index,
                "name": step.name,
                "status": step.status.value,
                "detail": step.detail,
                # step.data 是自由结构，保险起见把非 JSON 类型转成字符串
                # （以后哪个步骤塞个 Path 进来，不至于让接口 500）
                "data": {
                    k: (v if isinstance(v, (str, int, float, bool, type(None), list, dict)) else str(v))
                    for k, v in step.data.items()
                },
            }
        )

    def _run_one(self, cfg: Config, request: RunRequest, row: SheetRow) -> None:
        delivered = 0

        def on_step(step: StepResult) -> None:
            nonlocal delivered
            delivered += 1
            self._record_step(row.row_index, step)

        def on_writeback(_pipeline, update) -> Any:
            """把商品ID/状态写回表格。**整批只备份一次**。

            一行备份一次的话，一批 98 行会攒 98 份几乎一样的备份（还要靠 keep 去修剪），
            而真正该留住的是"这一批跑之前"的那一份 → 第一次写时备份，后面复用。
            """
            from temu_bot.sheet.writeback import write_back

            nonlocal backed_up
            report = write_back(cfg, update, backup_first=not backed_up)
            if report.ok:
                backed_up = True
            return report

        backed_up = False

        factory = self._pipeline_factory
        try:
            if factory is not None:
                # 注入了 factory 时回调可能没接上，所以下面还要按 outcome 补齐
                pipeline = factory(cfg, row, request)
            else:
                pipeline = ListingPipeline(
                    cfg,
                    row,
                    login=request.shop,
                    live=request.live,
                    op=request.op,
                    images_dir=request.images_dir,
                    swap_images=request.swap_images,
                    on_step=on_step,
                    should_cancel=lambda: self._cancel.is_set(),
                    on_writeback=on_writeback,
                )
            outcome = pipeline.run()
        except Exception as exc:  # noqa: BLE001
            log.exception("第 %s 行跑失败", row.row_index)
            self.results.append(RowResult(row_index=row.row_index, error=str(exc)))
            return

        # 回调没送到的那部分（或完全没接上回调）在这里补齐——
        # **最终步骤列表不能只依赖实时回调**，否则页面刷新后什么都看不到
        for step in outcome.steps[delivered:]:
            self._record_step(row.row_index, step)

        self.results.append(RowResult(row_index=row.row_index, outcome=outcome))
        self._record(cfg, request, row, outcome)

        # 只在**真实上架且成功**后才清理；干跑绝不清理
        if request.clean_images and request.live and not outcome.failed:
            self._clean(cfg, row, outcome, op=request.op)

    def _verify_pending(
        self, cfg: Config, request: RunRequest, rows: list[SheetRow]
    ) -> None:
        """批次结束后再读一次「处理中」的商品，把最终结果落回表格。

        为什么不在每行提交后一直等：Temu 发布可能要几分钟，逐行死等会把整批拖死。
        这里只做一次轻量复核——整批跑完时，先跑的行已经过去几分钟，足够定论了；
        还没定论的继续留「处理中」，不会假成功。
        """
        if not (request.live and request.op == OP_PUBLISH):
            return
        # 注入了假流水线（测试）时不做复核，避免碰真接口
        if self._pipeline_factory is not None:
            return

        pending = [
            r
            for r in self.results
            if r.outcome is not None
            and not r.outcome.failed
            and r.outcome.publish_state == "waiting"
            and r.outcome.product_id
        ]
        if not pending:
            return

        from temu_bot.sheet.writeback import statuses_of, updates_for, write_back

        rows_by_index = {row.row_index: row for row in rows}
        clients: dict[str, DxmClient | None] = {}
        try:
            for result in pending:
                outcome = result.outcome
                assert outcome is not None
                login = outcome.login or request.shop
                if login not in clients:
                    try:
                        clients[login] = DxmClient(cfg, login) if login else None
                    except Exception as exc:  # noqa: BLE001
                        log.warning("复核发布状态：建客户端失败（%s）：%s", login, exc)
                        clients[login] = None
                client = clients[login]
                if client is None:
                    continue

                try:
                    response = client.product_edit(outcome.product_id)
                except DxmError as exc:
                    log.warning("复核第 %s 行发布状态失败：%s", result.row_index, exc)
                    continue

                product = (response.get("data") or {}).get("product") or {}
                state = str(product.get("dxmState") or "")
                offline_state = str(product.get("dxmOfflineState") or "")
                err = str(product.get("errMsg") or "").strip()

                if offline_state == "publishFail" or err:
                    reason = err or "店小秘标记为发布失败（publishFail）"
                    step = StepResult(
                        "VERIFY",
                        Status.FAIL,
                        f"批次结束复核：发布失败：{reason}",
                        data={"state": state, "offline_state": offline_state, "error": err},
                    )
                    outcome.publish_state = "failed"
                elif state == "online":
                    step = StepResult(
                        "VERIFY",
                        Status.OK,
                        "批次结束复核：已发布上线",
                        data={"state": state, "offline_state": offline_state},
                    )
                    outcome.publish_state = "online"
                else:
                    continue

                outcome.steps.append(step)
                self._record_step(result.row_index, step)

                row = rows_by_index.get(result.row_index)
                if row is None:
                    continue
                update = updates_for(
                    row,
                    product_id=outcome.product_id,
                    failed=outcome.failed,
                    steps=outcome.steps,
                    statuses=statuses_of(cfg),
                )
                report = write_back(cfg, update, backup_first=False)
                if not report.ok:
                    log.warning(
                        "复核后的状态没写回第 %s 行：%s", result.row_index, report.message
                    )
                else:
                    log.info("复核第 %s 行：%s", result.row_index, report.message)
        finally:
            for client in clients.values():
                if client is not None:
                    client.close()

    def _site_changed_rows(self) -> list[int]:
        """哪些行踩到了「疑似接口变更」（SiteChanged）。"""
        rows: list[int] = []
        for result in self.results:
            outcome = result.outcome
            if outcome is None:
                continue
            if any(step.data.get("site_changed") for step in outcome.steps):
                rows.append(result.row_index)
        return rows

    def _write_site_change_report(self, cfg: Config, request: RunRequest) -> None:
        """有行踩到接口变更就留一份报告——给 `diagnose` / AI 排障用。"""
        rows = self._site_changed_rows()
        if not rows:
            return

        details: list[dict[str, Any]] = []
        for result in self.results:
            if result.outcome is None or result.row_index not in rows:
                continue
            for step in result.outcome.steps:
                if step.data.get("site_changed"):
                    details.append(
                        {"row": result.row_index, "step": step.name, "detail": step.detail}
                    )

        out_dir = cfg.data_dir / "reports" / "diagnose"
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-site-changed.json"
            payload = {
                "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                "login": request.shop,
                "live": request.live,
                "op": request.op,
                "rows": rows,
                "details": details,
                "endpoints_file": str(Path(cfg.root) / "config" / "dxm_endpoints.json"),
                "next": "跑 temu-bot diagnose api；按 docs/改版应对.md 处理",
            }
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            self.site_change_report = str(path)
            log.warning("有 %d 行疑似遇到店小秘接口变更，诊断报告：%s", len(rows), path)
        except OSError as exc:
            log.warning("写接口变更报告失败（不影响上架结果）：%s", exc)

    @staticmethod
    def _record(cfg: Config, request: RunRequest, row: SheetRow, outcome: ListingOutcome) -> None:
        """把结果追加进 data/state/runs.jsonl。

        主流程**不回写表格**，所以一旦真实跑完，product_id 只存在于这个账本里——
        没有它就是"复制了但没记账"，重跑那一行就是重复铺货。
        """
        path = cfg.data_dir / "state" / "runs.jsonl"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # 登录身份可能是自动认出来的，以 SHOP_CHECK 那步记下来的为准
            resolved = ""
            for step in outcome.steps:
                if step.name == "SHOP_CHECK" and step.data.get("login"):
                    resolved = str(step.data["login"])
                    break
            record = {
                "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                "row_index": row.row_index,
                "row_key": row.row_key,
                "shop": row.shop,
                "login": resolved or request.shop,
                "live": request.live,
                "op": request.op,
                "product_id": outcome.product_id,
                "failed": outcome.failed,
                "publish_state": outcome.publish_state,
                "steps": [s.name for s in outcome.steps],
            }
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            log.warning("结果记账失败（不影响上架结果）：%s", exc)

    def _clean(
        self, cfg: Config, row: SheetRow, outcome: ListingOutcome, *, op: int
    ) -> None:
        """清本地中间产物 + （发布成功时）清店小秘图片空间里这次传的图。"""
        if self._on_clean is not None:
            # 测试注入的清理器：不碰真实文件，也不碰图片空间
            self._on_clean(cfg, row.row_key)
            return

        target = cfg.work_dir / row.row_key
        if target.exists():
            try:
                shutil.rmtree(target)
                log.info("已清理第 %s 行的中间产物：%s", row.row_index, target)
            except OSError as exc:
                log.warning("清理失败（不影响上架结果）：%s", exc)

        # 图片空间：只删**这一次**上传的那几张，而且只在「立即发布」成功后删。
        # op=1 仅保存 / op=3 移入待发布 的商品是草稿，还引用着这些图，删了会坏。
        if not outcome.uploaded_urls:
            return
        if op != 2:
            log.info(
                "第 %s 行是 op=%s（不是立即发布），图片空间里的 %d 张图先留着",
                row.row_index,
                op,
                len(outcome.uploaded_urls),
            )
            return

        from temu_bot.web.album_clean import clean_uploaded_images

        result = clean_uploaded_images(cfg, outcome.login, outcome.uploaded_urls)
        if result.error:
            log.warning("第 %s 行%s", row.row_index, result.summary())
        else:
            log.info("第 %s 行%s", row.row_index, result.summary())

    def wait(self, timeout: float | None = None) -> None:
        """测试/CLI 用：等任务结束。"""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
