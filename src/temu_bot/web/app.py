"""FastAPI 装配与路由：本机网页控制台。

只 bind 127.0.0.1，并拦掉非本机的 Host/Origin——本地服务最怕的是你浏览器里
某个网页偷偷打 `http://127.0.0.1:8765`（DNS rebinding / CSRF）。

启动装配照抄 `cli._boot()` 的三步（load_config → ensure_dirs → setup_logging），
但不复用 `_boot` 本身——它耦合了 rich Console 与 typer.Exit。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from temu_bot import __version__
from temu_bot.config import Config, load_config
from temu_bot.logging_setup import setup_logging
from temu_bot.preflight import check_inputs
from temu_bot.sheet.table import SheetTable
from temu_bot.web.configio import (
    ConfigWriteError,
    config_summary,
    list_dir,
    write_local_config,
)
from temu_bot.web.combo_jobs import ComboBusy, ComboRequest, ComboRunner
from temu_bot.web.jobs import BusyError, JobRunner, RunRequest
from temu_bot.web.logs import LogBuffer
from temu_bot.web.shopcheck import precheck_shops

log = logging.getLogger(__name__)

def _static_dir() -> Path:
    """前端静态文件在哪。

    源码运行时就是 `web/static`；**打包成 exe 之后**（PyInstaller）纯 Python 模块可能
    被塞进 `base_library.zip`，`__file__` 就不是一个真实目录了，得改用 `sys._MEIPASS`
    （PyInstaller 解包目录）。两个位置都试一下，取存在的那个。
    """
    here = Path(__file__).resolve().parent / "static"
    if here.is_dir():
        return here
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        for guess in (Path(bundle) / "temu_bot" / "web" / "static", Path(bundle) / "static"):
            if guess.is_dir():
                return guess
    return here


STATIC_DIR = _static_dir()
LOCAL_HOSTNAMES = {"127.0.0.1", "localhost", "::1"}

# SSE 轮询间隔：只影响"日志出现到看见"的延迟，不影响正确性
SSE_POLL_SECONDS = 0.4

# 缩略图：只给页面看个样子，不用原图尺寸
THUMB_MAX_WIDTH = 1600
THUMB_DEFAULT_WIDTH = 360

# 真实执行时，**没有商品ID 的新行**允不允许跑？
#
# 现在允许了：图片上传已经接通（`step_upload_images` → 店小秘 COS → 换图），
# 新行会真的把表里那几张图换上去。
#
# 之前挡着的理由（"复制出来只改标题、图片还是模板的，会得到错商品"）现在由
# **流水线自己**兜住：`step_apply_fields` 在换图没写进任何字段时会直接 FAIL 中止，
# 不会存下去。所以这里不再需要一道粗闸门。
LIVE_NEW_ROW_ALLOWED = True


def _login_view(info: Any) -> dict[str, Any]:
    """把一个登录身份渲染成给页面看的样子。"""
    return {
        "login": info.login,
        "profile_dir": str(info.profile_dir),
        "session_file": str(info.session_file),
        "has_profile": info.has_profile,
        "has_session": info.has_session,
        # 配置里登记过就带一句提示；**过期了也不影响识别**，页面别拿它做判定
        "known": info.known,
        "shop_hint": info.shop_hint,
        "ready": info.ready,
    }


@dataclass
class AppState:
    cfg: Config
    logs: LogBuffer
    runner: JobRunner
    combo: ComboRunner = field(default_factory=ComboRunner)


# ------------------------------------------------------------------ 日志流
def initial_cursor(last_event_id: str | None, after: int) -> int:
    """算 SSE 从哪一条开始推。

    `EventSource` 断线重连时打的是**同一个 URL**（`?after=` 不会变），续传位置
    只能靠它自动带上的 `Last-Event-ID`。不认这个头就会每次重连都把整段日志重放一遍。
    """
    if last_event_id and last_event_id.strip().isdigit():
        return int(last_event_id)
    return max(0, after)


async def log_event_stream(buffer, *, after: int, is_disconnected, poll: float = SSE_POLL_SECONDS):
    """SSE 事件流。

    刻意用「异步生成器 + `asyncio.sleep` 轮询」，不用 `threading.Condition` 之类阻塞等待：
    后者会卡住 uvicorn 的 event loop（连带所有接口一起卡），而且客户端断线时
    被阻塞的线程没法及时取消，会一次次堆积。轮询这个量级完全够，断开即回收。
    """
    cursor = after
    # 让浏览器的重连间隔确定下来（默认 3s，写出来省得猜）
    yield "retry: 3000\n\n"
    while True:
        if await is_disconnected():
            return
        entries = buffer.since(cursor)
        if entries:
            for entry in entries:
                cursor = entry.seq
                data = json.dumps(entry.as_dict(), ensure_ascii=False)
                yield f"id: {cursor}\ndata: {data}\n\n"
        else:
            # 心跳：保持连接，也让浏览器知道服务还活着
            yield ": ping\n\n"
        await asyncio.sleep(poll)


# ------------------------------------------------------------------ 请求模型
class PreflightPayload(BaseModel):
    images_dir: str = ""
    limit: int | None = None
    skip_done: bool = False


class RunPayload(BaseModel):
    # 空的 = 不指定登录身份，由流水线按表格里的店铺名自动认
    shop: str = ""
    rows: list[int] = Field(default_factory=list)
    images_dir: str = ""
    live: bool = False
    op: int = 1
    clean_images: bool = False
    # None = 自动（新行换图、已有商品ID的行不重复换）
    swap_images: bool | None = None


class ShopCheckPayload(BaseModel):
    """上架前核对店铺：只传要跑的 Excel 行号，店铺名由后端从表里读。"""

    rows: list[int] = Field(default_factory=list)


class DiagnosePayload(BaseModel):
    """接口自检：可选行号（用来定位登录身份），可选深检。"""

    rows: list[int] = Field(default_factory=list)
    deep: bool = False


class ConfigPayload(BaseModel):
    changes: dict[str, Any] = Field(default_factory=dict)


class DialogPayload(BaseModel):
    title: str = ""
    initial: str = ""
    filters: str = ""


class CollectPayload(BaseModel):
    folder: str
    shop: str = ""
    template_spu: str = ""
    main_index: int = 1
    out: str = ""
    img_name_as_skc: bool = False
    force_material: bool = True     # 缺素材图默认用主图生成（2026-10-16 用户要求）
    material_width: int = 800
    material_height: int = 800
    recursive: bool = False
    # 「图片文件名清单」：给了就按 11 位 ID 把中文/英文标题填进第 17/18 列
    title_list: str = ""
    live: bool = False


COLLECT_PREVIEW_ROWS = 200


# ---------------------------------------------------------------- 工具箱
# 四个工具的参数都不一样，但都长在"一个文件夹 + 一些开关"上，所以共用一个基类。
class ToolboxFolderPayload(BaseModel):
    folder: str = ""
    live: bool = False


class ToolboxRenamePayload(ToolboxFolderPayload):
    mode: str = "id-only"
    id_length: int = 11
    csv_path: str = ""       # 只有 roundtrip 模式用得到
    export_format: str = "both"


class ToolboxExportPayload(ToolboxFolderPayload):
    csv_path: str = ""
    fmt: str = "both"        # both / csv / xlsx


class ToolboxConvertPayload(ToolboxFolderPayload):
    target_format: str = "png"
    quality: int = 100
    output_dir: str = ""     # 空 = 跟源文件同目录
    recursive: bool = False
    overwrite: bool = False


class ToolboxResizePayload(ToolboxFolderPayload):
    width: int = 1340
    height: int = 1785
    target_format: str = "jpg"
    fit: str = "cover"
    quality: int = 95
    output_dir: str = ""
    recursive: bool = False
    overwrite: bool = False
    # True（默认）= 覆盖原图、保持文件名；False = 另存 `<原名>_宽x高`
    inplace: bool = True


class ToolboxComboPayload(ToolboxFolderPayload):
    """尺寸与格式 → 11 位 ID 一条龙。"""

    width: int = 1340
    height: int = 1785
    target_format: str = "jpg"
    fit: str = "cover"
    quality: int = 95
    id_length: int = 11
    csv_path: str = ""
    export_format: str = "both"


class ToolboxMergePayload(ToolboxFolderPayload):
    output_file: str = ""
    add_source_row: bool = False


# ------------------------------------------------------------------ 装配
def _find_row(cfg: Config, row_index: int):
    """按 Excel 行号取一行（找不到返回 None）。"""
    try:
        table = SheetTable.from_config(cfg)
        for row in table.iter_rows(row_index=row_index):
            return row
    except Exception:  # noqa: BLE001
        log.warning("按行号读表失败", exc_info=True)
    return None


def build_state(cfg: Config | None = None, *, runner: JobRunner | None = None) -> AppState:
    cfg = cfg or load_config()
    cfg.ensure_dirs()
    setup_logging(cfg.data_dir / "logs", level=cfg.runtime.log_level, console=False)

    logs = LogBuffer()
    logs.attach()
    return AppState(cfg=cfg, logs=logs, runner=runner or JobRunner(), combo=ComboRunner())


def create_app(cfg: Config | None = None, *, state: AppState | None = None) -> FastAPI:
    app = FastAPI(title="temu-bot 控制台", version=__version__, docs_url=None, redoc_url=None)
    app.state.temu = state or build_state(cfg)

    # ---------------------------------------------------------- 本机限制
    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = request.headers.get("host", "")
        hostname = host.rsplit(":", 1)[0].strip("[]").lower()
        if hostname not in LOCAL_HOSTNAMES:
            log.warning("拒绝非本机 Host：%s", host)
            return JSONResponse({"error": f"只允许本机访问（Host={host}）"}, status_code=403)

        origin = request.headers.get("origin")
        if origin:
            parsed = urlparse(origin)
            if (parsed.hostname or "").lower() not in LOCAL_HOSTNAMES:
                log.warning("拒绝非本机 Origin：%s", origin)
                return JSONResponse({"error": f"只允许本机来源（Origin={origin}）"}, status_code=403)
        return await call_next(request)

    # ---------------------------------------------------------- 页面
    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        page = STATIC_DIR / "index.html"
        if not page.exists():
            return HTMLResponse("<h1>控制台静态文件缺失</h1>", status_code=500)
        return HTMLResponse(page.read_text(encoding="utf-8"))

    # ---------------------------------------------------------- 只读信息
    @app.get("/api/status")
    def api_status(request: Request) -> dict[str, Any]:
        st: AppState = request.app.state.temu
        cfg = st.cfg
        rows = 0
        table_error = ""
        try:
            rows = SheetTable.from_config(cfg).count()
        except Exception as exc:  # noqa: BLE001
            table_error = str(exc)

        return {
            "version": __version__,
            "root": str(cfg.root),
            "table": str(cfg.project.table),
            "table_rows": rows,
            "table_error": table_error,
            # 可用的**登录身份**：以磁盘上的实际痕迹为准（profiles/ + session_*.json），
            # 不看配置里那份静态店铺表——店铺会变、账号也会增减。
            "logins": [_login_view(i) for i in cfg.logins()],
            # 配置里登记的店铺：只是提示（收集页给个店名建议、设置页编辑用）
            "shops": [
                {
                    "name": s.name,
                    "login": s.login or "",
                    "profile_key": s.profile_key,
                    "template_spuid": s.template_spuid or "",
                }
                for s in cfg.shops
            ],
            "speed": cfg.runtime.speed,
            "dry_run": cfg.runtime.dry_run,
            "job": st.runner.snapshot(),
        }

    @app.get("/api/config")
    def api_config(request: Request) -> dict[str, Any]:
        st: AppState = request.app.state.temu
        return config_summary(st.cfg)

    @app.get("/api/doctor")
    def api_doctor(request: Request) -> dict[str, Any]:
        from temu_bot.doctor import has_failure, run_doctor

        st: AppState = request.app.state.temu
        checks = run_doctor(st.cfg)
        return {
            "failed": has_failure(checks),
            "checks": [{"name": c.name, "level": c.level, "detail": c.detail} for c in checks],
        }

    @app.get("/api/rows")
    def api_rows(
        request: Request,
        limit: int = Query(200, ge=1, le=2000),
        skip_done: bool = Query(False),
    ) -> dict[str, Any]:
        st: AppState = request.app.state.temu
        dropped: list[int] = []
        try:
            table = SheetTable.from_config(st.cfg)
            rows = [
                r.to_view()
                for r in table.iter_rows(limit=limit, skip_done=skip_done, dropped=dropped)
            ]
        except Exception as exc:  # noqa: BLE001
            return {"rows": [], "dropped": [], "dropped_count": 0, "error": str(exc)}
        # dropped = 表里那些"全空/缺文件夹或主图"的行，读表时就跳过了。
        # 报给前端只是让用户知道它们被跳开了，不是丢了数据。
        return {
            "rows": rows,
            "dropped": dropped,
            "dropped_count": len(dropped),
            "error": "",
        }

    @app.get("/api/fs/list")
    def api_fs_list(
        path: str = Query(""),
        images: bool = Query(False),
    ) -> dict[str, Any]:
        return list_dir(path, images_only=images)

    @app.post("/api/preflight")
    def api_preflight(request: Request, payload: PreflightPayload) -> dict[str, Any]:
        st: AppState = request.app.state.temu
        report = check_inputs(
            st.cfg,
            images_dir=Path(payload.images_dir) if payload.images_dir else None,
            limit=payload.limit,
            skip_done=payload.skip_done,
        )
        return {
            "ok": report.ok,
            "table_ok": report.table_ok,
            "table_detail": report.table_detail,
            "folder_ok": report.folder_ok,
            "folder_detail": report.folder_detail,
            "images_dir": str(report.images_dir) if report.images_dir else "",
            "image_count": report.image_count,
            "summary": report.summary(),
            "blocked": [
                {"row_index": r.row_index, "folder": str(r.folder), "problems": list(r.problems)}
                for r in report.blocked[:50]
            ],
            "blocked_total": len(report.blocked),
            "ready_total": len(report.ready),
        }

    @app.get("/api/row/{row_index}/images")
    def api_row_images(request: Request, row_index: int) -> JSONResponse:
        """这一行的图：找不找得到、合不合规、处理前处理后各是什么样（只读）。"""
        from temu_bot.imaging import index_folder, plan_row

        st: AppState = request.app.state.temu
        row = _find_row(st.cfg, row_index)
        if row is None:
            return JSONResponse({"error": f"表里没有第 {row_index} 行"}, status_code=404)

        folder = Path(row.folder) if row.folder else None
        index = index_folder(folder) if folder else None
        planned = plan_row(st.cfg, row, index=index)

        return JSONResponse(
            {
                "row_index": row_index,
                "folder": str(folder) if folder else "",
                "folder_exists": bool(index and index.exists),
                "folder_image_count": len(index.files) if index else 0,
                "title_cn": row.title_cn,
                "title_en": row.title_en,
                "product_id": row.product_id,
                "items": [
                    {
                        "slot": item.slot,
                        "spec": item.spec.key,
                        "spec_label": item.spec.label_with_grounding,
                        "target": f"{item.spec.width}x{item.spec.height}",
                        "name": item.src.name,
                        "action": item.action,
                        "note": item.note,
                        "found": item.before is not None and item.before.exists,
                        "size": item.before.size_label() if item.before else "",
                        "problems": list(item.before.problems) if item.before else ["找不到这张图"],
                    }
                    for item in planned
                ],
            }
        )

    @app.get("/api/row/{row_index}/thumb")
    def api_row_thumb(
        request: Request,
        row_index: int,
        slot: str = Query("main"),
        w: int = Query(THUMB_DEFAULT_WIDTH, ge=80, le=THUMB_MAX_WIDTH),
    ) -> Response:
        """这一行某张图的缩略图。找得到才给，找不到返回 404 并说明原因。"""
        import io

        from PIL import Image

        from temu_bot.imaging import index_folder

        st: AppState = request.app.state.temu
        row = _find_row(st.cfg, row_index)
        if row is None:
            return JSONResponse({"error": f"表里没有第 {row_index} 行"}, status_code=404)

        names = row.images.get(slot) or []
        if not names:
            return JSONResponse({"error": f"这一行没填「{slot}」图"}, status_code=404)

        folder = Path(row.folder) if row.folder else None
        found = index_folder(folder).resolve(names[0]) if folder else None
        if found is None or not found.ok:
            reason = found.describe() if found else "没有图片文件夹"
            return JSONResponse({"error": reason}, status_code=404)

        try:
            with Image.open(found.path) as opened:
                image = opened.convert("RGB")
                # 等比缩到目标宽度（高图也不会失真）
                scale = min(1.0, w / image.width)
                if scale < 1.0:
                    image = image.resize(
                        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                        Image.Resampling.LANCZOS,
                    )
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=85, optimize=True)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"这张图打不开：{exc}"}, status_code=415)

        return Response(
            content=buffer.getvalue(),
            media_type="image/jpeg",
            headers={"Cache-Control": "max-age=120"},
        )

    def _run_api_check(cfg: Config, rows: list[int], *, deep: bool = False) -> dict[str, Any]:
        """只读接口自检：先按表格行找出该用哪个登录身份，再逐项探活。"""
        from temu_bot.diagnose import check_api, pick_login, write_report

        logins: list[str] = []
        try:
            shop_check = precheck_shops(cfg, rows) if rows else {"checks": []}
            logins = [
                str(c.get("login"))
                for c in shop_check.get("checks", [])
                if c.get("login")
            ]
        except Exception:  # noqa: BLE001 - 自检不该因为核对失败而崩
            logins = []

        if not logins:
            login = pick_login(cfg, "")
            if login:
                logins = [login]

        if not logins:
            return {
                "ok": True,
                "logins": [],
                "reports": [],
                "note": "没有可检查的登录身份，跳过",
            }

        reports = []
        for login in dict.fromkeys(logins):
            report = check_api(cfg, login, deep=deep)
            try:
                write_report(cfg, report)
            except OSError as exc:  # noqa: PERF203
                log.warning("写接口自检报告失败：%s", exc)
            reports.append(report.as_dict())

        failed = [r for r in reports if not r.get("ok")]
        return {"ok": not failed, "logins": logins, "reports": reports, "note": ""}

    @app.post("/api/diagnose/api")
    def api_diagnose(request: Request, payload: DiagnosePayload) -> dict[str, Any]:
        """接口自检：登录态 / 商品列表 / 图片空间（可选商品详情）。**只读。**"""
        st: AppState = request.app.state.temu
        return _run_api_check(st.cfg, payload.rows, deep=payload.deep)

    @app.post("/api/shops/precheck")
    def api_shops_precheck(request: Request, payload: ShopCheckPayload) -> dict[str, Any]:
        """上架前先核对这批行用到的店铺。**只读，不启动任何上架动作。**

        用户 2026-10-17：把「核对提醒」和「上架执行」分开。
        一个店对不上就返回 `ok=false`，前端把话说清楚、**不点开始**。
        """
        st: AppState = request.app.state.temu
        if not payload.rows:
            return {"ok": False, "message": "还没勾选要跑的行", "checks": []}
        try:
            return precheck_shops(st.cfg, payload.rows)
        except Exception as exc:  # noqa: BLE001
            log.exception("店铺核对失败")
            return {"ok": False, "message": f"核对失败：{exc}", "checks": []}

    @app.post("/api/shops/verify")
    def api_shops_verify(
        request: Request,
        login: str = Query(..., description="登录身份（profiles/<这个>）"),
        expect: str = Query("", description="要核对的店铺名，多个用逗号分隔；一般直接传表格里用到的"),
    ) -> dict[str, Any]:
        """只读核对：**这个登录身份登录的账号里，实际有哪些店**。

        用户 2026-10-16：店铺不固定、登录的店铺随时会改，所以要**从上传表格里的
        店铺名**去对**网站登录账号里实际的店铺**。所以这里：

          - 一律实时读 `userIn.shopMap`（不听配置里写的那份静态表）
          - 传入的 `expect` 就是要核对的店名（表格里用到的）
          - 回话里既给"账号里有什么"，也给"缺了哪几个"
        """
        from temu_bot.dianxiaomi.client import (
            DxmClient,
            DxmError,
            NotLoggedIn,
            is_real_shop,
            normalize_shop_name,
        )

        st: AppState = request.app.state.temu
        wanted = [x.strip() for x in expect.replace("，", ",").split(",") if x.strip()]

        try:
            with DxmClient(st.cfg, login) as client:
                shops = client.shops()
                result = client.verify_shop(wanted) if wanted else None
        except (NotLoggedIn, DxmError) as exc:
            return {"ok": False, "message": f"接口不可用：{exc}", "shops": [], "missing": wanted}

        live = [
            {"name": str(s.get("name") or ""), "id": str(s.get("id") or "")}
            for s in shops
            if is_real_shop(s)
        ]
        names = [s["name"] for s in live]
        have = {normalize_shop_name(n) for n in names}
        missing = [w for w in wanted if normalize_shop_name(w) not in have]

        if not wanted:
            message = f"账号里现有的店：{'、'.join(names) or '（一个都没读到）'}"
            ok = bool(names)
        elif missing:
            message = (
                f"核对不通过：表里要用「{'、'.join(missing)}」，"
                f"但这个身份登录的账号里没有。账号里现有的店：{'、'.join(names) or '（一个都没读到）'}"
            )
            ok = False
        else:
            message = f"✅ 表里用到的店都在账号里：{'、'.join(wanted)}"
            ok = True

        return {
            "ok": ok,
            "message": message,
            "shops": live,
            "expected": wanted,
            "missing": missing,
            "matched_name": (result.matched_name if result and result.ok else ""),
            "matched_id": (str(result.matched_id) if result and result.ok else ""),
        }

    # ---------------------------------------------------------- 打开店小秘
    @app.post("/api/browser/open")
    def api_browser_open(
        request: Request,
        shop: str = Query("", description="表格里的店铺名；用它自动决定开哪个身份的浏览器"),
        login: str = Query("", description="也可以直接指定登录身份（不传就按 shop 自动认）"),
        url: str = Query("", description="起始地址；不传就用默认的店小秘入口"),
        wait: bool = Query(False, description="没有登录态时，等用户登录完成再返回"),
        wait_minutes: int = Query(10, ge=1, le=10, description="wait=1 时最多等多少分钟（上限 10）"),
    ) -> JSONResponse:
        """打开浏览器逛店小秘，好让你确认店铺 / 登录 / 切换店铺。

        用户不手选登录身份了，所以**传表格里的店铺名**，由程序决定开哪个身份
        （表里这家店在谁的账号里就开谁）。另起一个进程去做（不占服务线程），
        浏览器窗口关掉就自动收工。
        """
        from temu_bot.web.browser_launch import AlreadyOpen, open_for_shop, open_site

        st: AppState = request.app.state.temu
        try:
            if login:
                return JSONResponse(open_site(login, url=url or None))
            return JSONResponse(
                open_for_shop(
                    st.cfg,
                    shop,
                    url=url or None,
                    wait=wait,
                    wait_minutes=wait_minutes,
                )
            )
        except AlreadyOpen as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception as exc:  # noqa: BLE001
            log.exception("打开店小秘失败")
            return JSONResponse({"error": f"打开失败：{exc}"}, status_code=500)

    @app.get("/api/browser/status")
    def api_browser_status(login: str = Query("")) -> dict[str, Any]:
        from temu_bot.web.browser_launch import status as browser_status

        return browser_status(login or None)

    # ---------------------------------------------------------- 任务
    @app.post("/api/run")
    def api_run(request: Request, payload: RunPayload) -> JSONResponse:
        st: AppState = request.app.state.temu

        if not payload.rows:
            return JSONResponse({"error": "没选要跑的行"}, status_code=409)

        # **先核对店铺，再决定要不要开跑**（用户 2026-10-17 要求）。
        # 对不上就直接 409 返回，一行都不跑——绝不出现"98 行各刷一条同样的错误"。
        # 前端也会先调 /api/shops/precheck 把结果单独显示；这里是第二道闸，
        # 保证任何调用方（脚本、别的页面）都绕不过去。
        check = precheck_shops(st.cfg, payload.rows)
        if not check["ok"]:
            return JSONResponse(
                {"error": check["message"], "shop_check": check, "started": False},
                status_code=409,
            )

        # 第二道闸：**接口自检**。店小秘改版时，别跑到一半才发现保存接口没了。
        api_check = _run_api_check(st.cfg, payload.rows)
        if not api_check["ok"]:
            first = next(
                (
                    c
                    for r in api_check.get("reports", [])
                    for c in r.get("checks", [])
                    if not c.get("ok")
                ),
                None,
            )
            detail = f"{first['name']}：{first['detail']}" if first else ""
            return JSONResponse(
                {
                    "error": (
                        f"接口自检没通过，已停止上架：{detail}。"
                        f"先跑 `temu-bot diagnose api` 看报告，按 docs/改版应对.md 处理。"
                    ),
                    "api_check": api_check,
                    "started": False,
                },
                status_code=409,
            )

        # 真实执行前的硬闸：新行（还没有商品ID）——现在放行了，闸门本身保留着
        # （`LIVE_NEW_ROW_ALLOWED = True`，要临时关掉就改那个常量）。
        if payload.live and not LIVE_NEW_ROW_ALLOWED:
            fresh = []
            for index in payload.rows:
                row = _find_row(st.cfg, index)
                if row is not None and not row.product_id:
                    fresh.append(index)
            if fresh:
                shown = "、".join(str(i) for i in fresh[:8])
                more = f" 等 {len(fresh)} 行" if len(fresh) > 8 else " 行"
                return JSONResponse(
                    {
                        "error": (
                            f"第 {shown}{more} 还没有商品ID，属于「新建商品」。"
                            f"这条闸现在被关掉了（LIVE_NEW_ROW_ALLOWED = False），"
                            f"这些行请先用干跑。"
                        ),
                        "blocked_rows": fresh,
                    },
                    status_code=409,
                )

        try:
            st.runner.start(
                st.cfg,
                RunRequest(
                    shop=payload.shop,
                    row_indexes=list(payload.rows),
                    images_dir=Path(payload.images_dir) if payload.images_dir else None,
                    live=payload.live,
                    op=payload.op,
                    clean_images=payload.clean_images,
                    swap_images=payload.swap_images,
                ),
            )
        except BusyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        mode = "真实执行" if payload.live else "干跑"
        log.info("已启动任务（%s）：%s 行，登录身份 %s", mode, len(payload.rows), payload.shop)
        return JSONResponse({"ok": True, "mode": mode})

    @app.get("/api/jobs/current")
    def api_job(request: Request) -> dict[str, Any]:
        st: AppState = request.app.state.temu
        return st.runner.snapshot()

    @app.post("/api/jobs/cancel")
    def api_cancel(request: Request) -> dict[str, Any]:
        st: AppState = request.app.state.temu
        return {"message": st.runner.request_cancel()}

    # ---------------------------------------------------------- 配置写
    @app.post("/api/config")
    def api_config_save(request: Request, payload: ConfigPayload) -> JSONResponse:
        st: AppState = request.app.state.temu
        try:
            path = write_local_config(st.cfg, payload.changes)
        except ConfigWriteError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

        # 重新加载整份配置并整体替换（Config 之后只读使用，不做原地改）
        try:
            st.cfg = load_config()
            st.cfg.ensure_dirs()
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"已写入但重新加载失败：{exc}"}, status_code=500)
        return JSONResponse({"ok": True, "path": str(path)})

    @app.get("/api/midou")
    def api_midou(request: Request, shop: str = Query(...)) -> dict[str, Any]:
        """米豆余额。只读，仅供参考——**复制商品实测不扣这里的额度**。"""
        from temu_bot.dianxiaomi.client import DxmClient, DxmError, NotLoggedIn

        st: AppState = request.app.state.temu
        try:
            with DxmClient(st.cfg, shop) as client:
                data = client.midou_balance()
        except (NotLoggedIn, DxmError) as exc:
            return {"ok": False, "error": str(exc), "items": []}

        view = (data or {}).get("viewMap") or {}
        items = [
            {"type": str(k), "available": (v or {}).get("availableCount")}
            for k, v in sorted(view.items(), key=lambda kv: str(kv[0]))
        ]
        return {"ok": True, "items": items}

    # ---------------------------------------------------------- 系统文件对话框
    # 浏览器不允许网页拿到本地真实路径，所以由**后端**在本机弹出系统对话框，
    # 把选中的路径回给页面。请求会一直挂到用户选完（或取消）为止——
    # 同步端点跑在 FastAPI 的线程池里，不会卡住 event loop。
    @app.post("/api/dialog/folder")
    def api_dialog_folder(payload: DialogPayload) -> JSONResponse:
        from temu_bot.web.dialogs import DialogBusy, pick_folder

        try:
            path = pick_folder(title=payload.title or "选择文件夹", initial=payload.initial)
        except DialogBusy as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        except Exception as exc:  # noqa: BLE001
            log.warning("弹文件夹对话框失败：%s", exc)
            return JSONResponse({"error": str(exc)}, status_code=500)
        return JSONResponse({"path": path, "cancelled": not path})

    @app.post("/api/dialog/file")
    def api_dialog_file(payload: DialogPayload) -> JSONResponse:
        from temu_bot.web.dialogs import DialogBusy, pick_file

        try:
            path = pick_file(
                title=payload.title or "选择文件",
                initial=payload.initial,
                filter_spec=payload.filters,
            )
        except DialogBusy as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        except Exception as exc:  # noqa: BLE001
            log.warning("弹文件对话框失败：%s", exc)
            return JSONResponse({"error": str(exc)}, status_code=500)
        return JSONResponse({"path": path, "cancelled": not path})

    @app.post("/api/dialog/save")
    def api_dialog_save(payload: DialogPayload) -> JSONResponse:
        from temu_bot.web.dialogs import DialogBusy, save_file

        try:
            path = save_file(
                title=payload.title or "保存为",
                initial=payload.initial,
                filter_spec=payload.filters,
            )
        except DialogBusy as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        except Exception as exc:  # noqa: BLE001
            log.warning("弹保存对话框失败：%s", exc)
            return JSONResponse({"error": str(exc)}, status_code=500)
        return JSONResponse({"path": path, "cancelled": not path})

    # ---------------------------------------------------------- 收集图片
    def _collect_options(payload: CollectPayload):
        from temu_bot.collect import CollectOptions

        return CollectOptions(
            folder=Path(payload.folder),
            shop=payload.shop,
            template_spu=payload.template_spu,
            main_image_index=payload.main_index,
            use_img_name_as_skc=payload.img_name_as_skc,
            force_gen_material=payload.force_material,
            material_size=(max(1, payload.material_width), max(1, payload.material_height)),
            recursive=payload.recursive,
            title_list=Path(payload.title_list) if payload.title_list.strip() else None,
        )

    @app.post("/api/collect/scan")
    def api_collect_scan(request: Request, payload: CollectPayload) -> JSONResponse:
        """只读扫描：会收成哪些行、跳过哪些。**不写任何文件。**"""
        from temu_bot.collect import (
            check_main_images,
            default_output,
            find_unknown_shop,
            safe_output_path,
            scan_folder,
        )

        st: AppState = request.app.state.temu
        options = _collect_options(payload)

        if not payload.folder:
            return JSONResponse({"error": "还没选素材文件夹"}, status_code=400)

        result = scan_folder(options)
        target = Path(payload.out) if payload.out else default_output(options.folder)
        final, will_rename = safe_output_path(target)
        # 预览里别显示一个"具体但会变"的时间戳——真正的时间戳是写入那一刻才生成的，
        # 显示出来只会让人觉得"怎么跟我看到的不一样"。给个模式就好。
        preview_out = (
            str(target.with_name(f"{target.stem}-<时间戳>{target.suffix}")) if will_rename else str(target)
        )

        # 顺带查一眼主图规格：不合规的行现在就告诉你，别等上架被平台打回
        problems = check_main_images(result)

        # 店铺名写错了会一路错到底（上架时拿它核对店铺），所以现在就提醒
        unknown_shop = find_unknown_shop(st.cfg, payload.shop)

        # 标题清单的匹配情况（没选清单就给空字段）
        title_info: dict[str, Any] = {}
        if result.title_list is not None:
            tl = result.title_list
            missing = [r.sku_prefix for r in result.rows if not (r.title_cn or r.title_en)]
            title_info = {
                "path": str(tl.path),
                "ok": tl.ok,
                "error": tl.error,
                "total": tl.rows,
                "with_en": tl.with_en,
                "shifted": tl.shifted,
                "matched": result.titled_rows,
                "missing": missing[:50],
                "missing_count": len(missing),
            }

        return JSONResponse(
            {
                "folder": str(options.folder),
                "image_count": result.image_count,
                "row_count": len(result.rows),
                "skipped_count": len(result.skipped),
                "spec_problem_count": len(problems),
                "summary": result.summary(),
                "unknown_shop": unknown_shop,
                "known_shops": [s.name for s in st.cfg.shops],
                "title_list": title_info,
                "rows": [
                    {
                        "index": r.index,
                        "main": r.main_name,
                        "material": r.material_name,
                        "stem": r.stem,
                        "note": r.note,
                        "problems": problems.get(r.main_name, []),
                        "title_cn": r.title_cn,
                        "title_en": r.title_en,
                    }
                    for r in result.rows[:COLLECT_PREVIEW_ROWS]
                ],
                "rows_truncated": max(0, len(result.rows) - COLLECT_PREVIEW_ROWS),
                "skipped": [{"name": s.name, "reason": s.reason} for s in result.skipped[:50]],
                "out_path": preview_out,
                "out_renamed": will_rename,
                "out_original": str(target) if will_rename else "",
                "shop": payload.shop,
                "template_spu": payload.template_spu,
            }
        )

    @app.post("/api/collect/run")
    def api_collect_run(request: Request, payload: CollectPayload) -> JSONResponse:
        """真的写出表格。**永远是新文件**，不会覆盖已有表。"""
        from temu_bot.collect import default_output, scan_folder, write_table

        st: AppState = request.app.state.temu
        options = _collect_options(payload)

        if not payload.folder:
            return JSONResponse({"error": "还没选素材文件夹"}, status_code=400)
        if not payload.live:
            return JSONResponse({"error": "这是写文件的操作，需要明确确认"}, status_code=400)

        result = scan_folder(options)
        if not result.ok:
            return JSONResponse(
                {"error": f"一张图都没收到，没东西可写。（{result.summary()}）"}, status_code=400
            )

        try:
            written = write_table(
                result,
                Path(payload.out) if payload.out else default_output(options.folder),
                generate_missing_material=payload.force_material,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("写表失败")
            return JSONResponse({"error": f"写表失败：{exc}"}, status_code=500)

        return JSONResponse(
            {
                "ok": True,
                "path": str(written.path),
                "rows": written.rows,
                "renamed": written.renamed,
                "original_target": str(written.original_target) if written.original_target else "",
                "generated_materials": written.generated_materials,
                "titles_filled": result.titled_rows,
                "summary": written.summary(),
            }
        )

    # ---------------------------------------------------------- 工具箱
    #
    # 四个工具都是「先 scan（干跑预览、一个字节都不写）再 run（要显式 live）」。
    # 逻辑全在 temu_bot.toolbox 里，这里只做参数搬运与错误包装。
    def _toolbox_call(kind: str, payload: Any, cfg: Config, *, dry_run: bool) -> JSONResponse:
        from temu_bot import toolbox

        folder = Path(payload.folder)
        if not folder.is_dir():
            return JSONResponse({"error": f"文件夹不存在：{folder}"}, status_code=400)

        csv_path = Path(payload.csv_path) if getattr(payload, "csv_path", "") else None
        output_dir = Path(payload.output_dir) if getattr(payload, "output_dir", "") else None
        output_file = Path(payload.output_file) if getattr(payload, "output_file", "") else None

        try:
            if kind == "rename":
                fn = toolbox.rename.run if not dry_run else toolbox.rename.plan
                report = fn(
                    cfg,
                    folder,
                    payload.mode,
                    id_length=payload.id_length,
                    csv_path=csv_path,
                    export_format=payload.export_format,
                )
            elif kind == "export":
                fn = toolbox.listing.run if not dry_run else toolbox.listing.plan
                report = fn(folder, csv_path=csv_path, fmt=payload.fmt)
            elif kind == "convert":
                fn = toolbox.convert.run if not dry_run else toolbox.convert.plan
                report = fn(
                    folder,
                    target_format=payload.target_format,
                    quality=payload.quality,
                    output_dir=output_dir,
                    recursive=payload.recursive,
                    overwrite=payload.overwrite,
                )
            elif kind == "resize":
                fn = toolbox.resize.run if not dry_run else toolbox.resize.plan
                report = fn(
                    folder,
                    width=payload.width,
                    height=payload.height,
                    target_format=payload.target_format,
                    fit=payload.fit,
                    quality=payload.quality,
                    output_dir=output_dir,
                    recursive=payload.recursive,
                    overwrite=payload.overwrite,
                    inplace=payload.inplace,
                )
            else:
                fn = toolbox.merge.run if not dry_run else toolbox.merge.plan
                report = fn(folder, output_file=output_file, add_source_row=payload.add_source_row)
        except Exception as exc:  # noqa: BLE001 —— 工具里的意外不该把接口打成 500 空白
            log.exception("工具箱 %s 出错", kind)
            return JSONResponse({"error": f"{kind} 出错：{exc}"}, status_code=500)

        return JSONResponse({"tool": kind, "folder": str(folder), **report.to_view()})

    @app.get("/api/toolbox/probe")
    def api_toolbox_probe(request: Request) -> dict[str, Any]:
        """工具箱自检：台账在哪 / 有多少个号 / Pillow 支不支持那几个格式。"""
        from temu_bot import toolbox

        return toolbox.selfcheck(request.app.state.temu.cfg)

    @app.post("/api/toolbox/rename/scan")
    def api_toolbox_rename_scan(request: Request, payload: ToolboxRenamePayload) -> JSONResponse:
        return _toolbox_call("rename", payload, request.app.state.temu.cfg, dry_run=True)

    @app.post("/api/toolbox/rename/run")
    def api_toolbox_rename_run(request: Request, payload: ToolboxRenamePayload) -> JSONResponse:
        if not payload.live:
            return JSONResponse({"error": "这是会改文件名的操作，需要明确确认"}, status_code=400)
        return _toolbox_call("rename", payload, request.app.state.temu.cfg, dry_run=False)

    @app.post("/api/toolbox/export/scan")
    def api_toolbox_export_scan(request: Request, payload: ToolboxExportPayload) -> JSONResponse:
        return _toolbox_call("export", payload, request.app.state.temu.cfg, dry_run=True)

    @app.post("/api/toolbox/export/run")
    def api_toolbox_export_run(request: Request, payload: ToolboxExportPayload) -> JSONResponse:
        if not payload.live:
            return JSONResponse({"error": "这是写文件的操作，需要明确确认"}, status_code=400)
        return _toolbox_call("export", payload, request.app.state.temu.cfg, dry_run=False)

    @app.post("/api/toolbox/convert/scan")
    def api_toolbox_convert_scan(request: Request, payload: ToolboxConvertPayload) -> JSONResponse:
        return _toolbox_call("convert", payload, request.app.state.temu.cfg, dry_run=True)

    @app.post("/api/toolbox/convert/run")
    def api_toolbox_convert_run(request: Request, payload: ToolboxConvertPayload) -> JSONResponse:
        if not payload.live:
            return JSONResponse({"error": "这是写文件的操作，需要明确确认"}, status_code=400)
        return _toolbox_call("convert", payload, request.app.state.temu.cfg, dry_run=False)

    @app.post("/api/toolbox/resize/scan")
    def api_toolbox_resize_scan(request: Request, payload: ToolboxResizePayload) -> JSONResponse:
        return _toolbox_call("resize", payload, request.app.state.temu.cfg, dry_run=True)

    @app.post("/api/toolbox/resize/run")
    def api_toolbox_resize_run(request: Request, payload: ToolboxResizePayload) -> JSONResponse:
        if not payload.live:
            return JSONResponse({"error": "这是写文件的操作，需要明确确认"}, status_code=400)
        return _toolbox_call("resize", payload, request.app.state.temu.cfg, dry_run=False)

    @app.post("/api/toolbox/combo/scan")
    def api_toolbox_combo_scan(
        request: Request, payload: ToolboxComboPayload
    ) -> JSONResponse:
        """一条龙预览：尺寸与格式 + 11 位 ID 都只模拟，一个字节都不写。"""
        from temu_bot import toolbox

        st: AppState = request.app.state.temu
        folder = Path(payload.folder)
        if not folder.is_dir():
            return JSONResponse({"error": f"文件夹不存在：{folder}"}, status_code=400)
        try:
            report = toolbox.combo.plan(
                st.cfg,
                folder,
                width=payload.width,
                height=payload.height,
                target_format=payload.target_format,
                fit=payload.fit,
                quality=payload.quality,
                id_length=payload.id_length,
                csv_path=Path(payload.csv_path) if payload.csv_path else None,
                export_format=payload.export_format,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("工具箱 combo 预览出错")
            return JSONResponse({"error": f"预览出错：{exc}"}, status_code=500)
        return JSONResponse({"tool": "combo", "folder": str(folder), **report.to_view()})

    @app.post("/api/toolbox/combo/start")
    def api_toolbox_combo_start(
        request: Request, payload: ToolboxComboPayload
    ) -> JSONResponse:
        """真执行（后台线程）：先尺寸与格式，再 11 位 ID + 导清单 + 还原。"""
        st: AppState = request.app.state.temu
        try:
            return JSONResponse(
                st.combo.start(
                    st.cfg,
                    ComboRequest(
                        folder=payload.folder,
                        width=payload.width,
                        height=payload.height,
                        target_format=payload.target_format,
                        fit=payload.fit,
                        quality=payload.quality,
                        id_length=payload.id_length,
                        csv_path=payload.csv_path,
                        export_format=payload.export_format,
                    ),
                )
            )
        except ComboBusy as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception as exc:  # noqa: BLE001
            log.exception("启动 combo 失败")
            return JSONResponse({"error": f"启动失败：{exc}"}, status_code=500)

    @app.get("/api/toolbox/combo/status")
    def api_toolbox_combo_status(request: Request) -> dict[str, Any]:
        return request.app.state.temu.combo.snapshot()

    @app.post("/api/toolbox/merge/scan")
    def api_toolbox_merge_scan(request: Request, payload: ToolboxMergePayload) -> JSONResponse:
        return _toolbox_call("merge", payload, request.app.state.temu.cfg, dry_run=True)

    @app.post("/api/toolbox/merge/run")
    def api_toolbox_merge_run(request: Request, payload: ToolboxMergePayload) -> JSONResponse:
        if not payload.live:
            return JSONResponse({"error": "这是写文件的操作，需要明确确认"}, status_code=400)
        return _toolbox_call("merge", payload, request.app.state.temu.cfg, dry_run=False)

    # ---------------------------------------------------------- 日志
    @app.get("/api/logs")
    def api_logs(request: Request, after: int = Query(0, ge=0), limit: int = Query(500, ge=1, le=2000)):
        st: AppState = request.app.state.temu
        entries = st.logs.buffer.since(after, limit=limit)
        return {
            "entries": [e.as_dict() for e in entries],
            "latest_seq": st.logs.buffer.latest_seq(),
        }

    @app.get("/api/logs/stream")
    async def api_logs_stream(request: Request, after: int = Query(0, ge=0)):
        st: AppState = request.app.state.temu
        cursor = initial_cursor(request.headers.get("last-event-id"), after)
        return StreamingResponse(
            log_event_stream(
                st.logs.buffer,
                after=cursor,
                is_disconnected=request.is_disconnected,
            ),
            media_type="text/event-stream; charset=utf-8",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
        )

    return app


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
    cfg: Config | None = None,
) -> None:
    """起服务。**刻意只 bind 本机**，不接受 0.0.0.0。"""
    import uvicorn

    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError(f"控制台只允许绑定本机地址，收到：{host}")

    url = f"http://127.0.0.1:{port}"
    print(f"\n  控制台地址：{url}")
    print("  只在本机可用；按 Ctrl+C 停止\n")

    if open_browser:
        import threading
        import webbrowser

        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    uvicorn.run(create_app(cfg), host=host, port=port, log_level="warning")
