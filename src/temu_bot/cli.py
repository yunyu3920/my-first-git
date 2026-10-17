"""命令行入口：doctor / config / table。"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from temu_bot import __version__
from temu_bot.browser.base import BrowserError
from temu_bot.browser.detect import describe_one_line
from temu_bot.browser.inspect import collect, dump_page, login_start_url, summarize
from temu_bot.browser.playwright_local import PlaywrightLocalProvider
from temu_bot.config import Config, ConfigError, ShopCfg, config_paths, load_config, mask_secret
from temu_bot.dianxiaomi.client import DxmClient, DxmError, NotLoggedIn
from temu_bot.dianxiaomi.session import load_session, save_session_from_context, session_path
from temu_bot.doctor import FAIL, PASS, WARN, has_failure, run_doctor
from temu_bot.logging_setup import setup_logging

# 守着浏览器时每隔几秒存一次登录态（cookie 只在浏览器还开着时读得到）
HOLD_SAVE_SECONDS = 5.0
from temu_bot.sheet.schema import COL_PRODUCT_ID, COL_STATUS, iter_missing_files
from temu_bot.sheet.table import SheetTable

console = Console()

DXM_BASE = "https://www.dianxiaomi.com"

app = typer.Typer(add_completion=False, no_args_is_help=True, help="自建 Temu 自动上架工具")
config_app = typer.Typer(no_args_is_help=True, help="查看配置")
table_app = typer.Typer(no_args_is_help=True, help="商品信息表读写")
browser_app = typer.Typer(no_args_is_help=True, help="浏览器启动与页面探测")
session_app = typer.Typer(no_args_is_help=True, help="登录态（接口客户端用）")
dxm_app = typer.Typer(no_args_is_help=True, help="店小秘接口（只读命令可直接用）")
images_app = typer.Typer(no_args_is_help=True, help="图片管线（按 Temu 规格出图，原图只读）")
toolbox_app = typer.Typer(
    no_args_is_help=True, help="图片工具箱（SKU 重命名 / 导出清单 / 格式转换 / Excel 合并）"
)
app.add_typer(config_app, name="config")
app.add_typer(table_app, name="table")
app.add_typer(browser_app, name="browser")
app.add_typer(session_app, name="session")
app.add_typer(dxm_app, name="dxm")
app.add_typer(images_app, name="images")
app.add_typer(toolbox_app, name="toolbox")

LEVEL_STYLE = {PASS: "green", WARN: "yellow", FAIL: "red"}


# ------------------------------------------------------------------ 启动
def _watch_requests(recorder: Any, stop: "threading.Event", *, interval: float = 8.0) -> None:
    """后台盯着"录到多少条请求"，周期性打印——让用户一眼看出操作有没有被录进来。

    录到 0 条最常见的原因：用户在**自己平时的浏览器**里操作，而不是工具打开的那个窗口。
    """

    def loop() -> None:
        waited = 0.0
        warned = False
        while not stop.wait(interval):
            waited += interval
            count = recorder.request_count
            if count:
                console.print(f"  [green]⏱ 已录到 {count} 条请求[/green]（继续操作，完成后按回车）")
                continue
            if waited >= interval * 3 and not warned:
                warned = True
                console.print(
                    "\n[yellow]⏱ 还没录到任何请求[/yellow] —— 请确认你操作的是"
                    "**工具刚打开的那个 Chrome**（顶部有 --no-sandbox 黄条），"
                    "不是你平时用的 Chrome！\n"
                )
            else:
                console.print("  [dim]⏱ 已录到 0 条请求…[/dim]")

    thread = threading.Thread(target=loop, daemon=True, name="record-watch")
    thread.start()


def _ask(prompt: str) -> str:
    """等待用户按键。双击 .bat 启动时 stdin 可能不是交互式的，这里兜住别抛栈。"""
    try:
        return console.input(prompt)
    except (EOFError, KeyboardInterrupt):
        console.print("\n[yellow]没读到输入（窗口不是交互式的），按空回车继续处理。[/yellow]")
        return ""


def _boot(log_level: str | None = None, *, console_log: bool = False) -> Config:
    try:
        cfg = load_config()
    except ConfigError as exc:
        console.print(f"[red]配置错误：{exc}[/red]")
        raise typer.Exit(2) from exc

    cfg.ensure_dirs()
    setup_logging(cfg.data_dir / "logs", level=log_level or cfg.runtime.log_level, console=console_log)
    return cfg


@app.callback()
def _root() -> None:
    """temu-bot —— 只服务自有店铺的 Temu 上架自动化（默认 dry-run）。"""


@app.command()
def version() -> None:
    """打印版本号。"""
    typer.echo(f"temu-bot {__version__}")


# ------------------------------------------------------------------ doctor
@app.command()
def doctor(
    as_json: bool = typer.Option(False, "--json", help="输出 JSON（给脚本/控制台用）"),
) -> None:
    """环境自检：表、工具箱、浏览器、AI key、目录、店铺登录态。"""
    cfg = _boot()

    checks = run_doctor(cfg)

    if as_json:
        console.print_json(json.dumps([c.__dict__ for c in checks], ensure_ascii=False))
    else:
        t = Table(title="temu-bot doctor", show_lines=False, header_style="bold")
        t.add_column("检查项", style="cyan", no_wrap=True)
        t.add_column("结果", no_wrap=True)
        t.add_column("详情", overflow="fold")
        for c in checks:
            t.add_row(c.name, f"[{LEVEL_STYLE[c.level]}]{c.level}[/]", c.detail)
        console.print(t)

    if has_failure(checks):
        raise typer.Exit(1)


# ------------------------------------------------------------------ config
@config_app.command("paths")
def config_paths_cmd() -> None:
    """打印配置文件位置与是否存在。"""
    main, local = config_paths()
    for label, path in (("主配置", main), ("本机覆盖", local)):
        mark = "存在" if path.exists() else "不存在"
        console.print(f"{label}: {path}  [{('green' if path.exists() else 'dim')}]{mark}[/]")


@config_app.command("show")
def config_show() -> None:
    """打印生效配置（密钥只显示掩码，不落原文）。"""
    cfg = _boot()
    data = json.loads(cfg.model_dump_json(indent=2))

    # 把 provider 的密钥换成掩码
    for provider in data.get("ai", {}).get("providers", []):
        env_name = provider.get("api_key_env", "")
        provider["api_key"] = mask_secret(os.environ.get(env_name))
    for shop in data.get("shops", []):
        shop["logged_in"] = bool(shop.get("profile_dir") and Path(shop["profile_dir"]).exists()
                                 and any(Path(shop["profile_dir"]).iterdir()))

    console.print_json(json.dumps(data, ensure_ascii=False))


# ------------------------------------------------------------------ table
@table_app.command("headers")
def table_headers() -> None:
    """校验表头与列契约是否一致（列漂移会让回写打错列）。"""
    cfg = _boot()
    st = SheetTable.from_config(cfg)
    header = st.read_headers()
    problems = st.validate_headers()

    t = Table(title=f"{cfg.project.table.name} / {cfg.project.sheet_name}", header_style="bold")
    t.add_column("#", justify="right", style="dim")
    t.add_column("列名")
    for col in sorted(header):
        t.add_row(str(col), header[col])
    console.print(t)

    if problems:
        console.print(f"[red]与契约不一致（{len(problems)} 处）：[/red]")
        for p in problems:
            console.print(f"  - {p}")
        raise typer.Exit(1)
    console.print("[green]表头与列契约完全一致[/green]")


@table_app.command("stats")
def table_stats() -> None:
    """行数 / 状态分布 / 店铺分布。"""
    cfg = _boot()
    stats = SheetTable.from_config(cfg).stats()
    console.print_json(json.dumps(stats, ensure_ascii=False))


@table_app.command("writeback")
def table_writeback(
    from_log: str = typer.Option(
        "data/state/live_runs.jsonl",
        "--from",
        help="从哪个运行日志里取「行号 → 商品ID」（tools/live_run_rows.py 写的那个）",
    ),
    status: str = typer.Option("成功", "--status", help="写进第 14 列「状态」的值"),
    live: bool = typer.Option(False, "--live", help="真写；默认只报告将要写什么"),
) -> None:
    """把跑完的商品ID 写回表格（第 13 列 + 状态第 14 列）。

    **为什么要写回**：表格里没有商品ID 的行，在流水线眼里就是"新建"。
    同一行再跑一次会**再复制一遍模板、再建一个重复商品**——写回是防重复铺货的闸门。

    写前会备份（`data/backups/`），并确认表没被 Excel/WPS 打开；落盘是原子替换。
    """
    from temu_bot.sheet.guard import CellUpdate, TableBusyError, assert_not_busy, write_updates

    cfg = _boot()
    path = Path(from_log)
    if not path.exists():
        console.print(f"[red]找不到运行日志 {path}[/red]")
        raise typer.Exit(1)

    done: dict[int, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if item.get("ok") and item.get("product_id"):
            done[int(item["row_index"])] = str(item["product_id"])

    if not done:
        console.print(f"[yellow]{path} 里没有成功的记录[/yellow]")
        raise typer.Exit(0)

    table = SheetTable.from_config(cfg)
    existing = {r.row_index: r for r in table.iter_rows()}

    updates: list[CellUpdate] = []
    for row_index, product_id in sorted(done.items()):
        row = existing.get(row_index)
        if row is None:
            console.print(f"[yellow]第 {row_index} 行现在不在表里，跳过[/yellow]")
            continue
        if row.product_id == product_id:
            console.print(f"[dim]第 {row_index} 行已经是 {product_id}，不用写[/dim]")
            continue
        if row.product_id and row.product_id != product_id:
            console.print(
                f"[red]第 {row_index} 行表里已有商品ID {row.product_id}，"
                f"与日志里的 {product_id} 不同——不覆盖，请人工确认[/red]"
            )
            continue
        updates.append(
            CellUpdate(
                row_index=row_index,
                fields={COL_PRODUCT_ID: product_id, COL_STATUS: status},
            )
        )

    if not updates:
        console.print("[green]没有需要回写的行[/green]")
        raise typer.Exit(0)

    t = Table(title=f"将要写回 {len(updates)} 行" + ("" if live else "（干跑）"))
    t.add_column("行")
    t.add_column("商品ID")
    t.add_column("状态")
    for update in updates:
        t.add_row(
            str(update.row_index),
            str(update.fields[COL_PRODUCT_ID]),
            str(update.fields[COL_STATUS]),
        )
    console.print(t)

    if not live:
        console.print("\n[cyan]干跑：确认无误再加 --live 真写（写前会先备份）。[/cyan]")
        raise typer.Exit(0)

    try:
        assert_not_busy(cfg.project.table)
        backup_path = write_updates(cfg, updates)
    except TableBusyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    console.print(f"[green]已写回 {len(updates)} 行[/green]｜备份 {backup_path}")


@table_app.command("read")
def table_read(
    limit: int = typer.Option(3, "--limit", "-n", help="最多打印几行"),
    row_key: str | None = typer.Option(None, "--row-key", help="只读指定 row_key"),
    row_index: int | None = typer.Option(None, "--row-index", help="只读指定 Excel 行号"),
    status: str | None = typer.Option(None, "--status", help="只读指定状态，如 成功"),
    shop: str | None = typer.Option(None, "--shop", help="只读指定店铺"),
    skip_done: bool = typer.Option(False, "--skip-done", help="跳过状态=成功的行"),
    check_files: bool = typer.Option(False, "--files", help="顺带检查图片文件是否存在"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """读取商品表的数据行（P0 验收命令）。"""
    cfg = _boot()
    st = SheetTable.from_config(cfg)

    rows = list(
        st.iter_rows(
            limit=limit,
            row_key=row_key,
            row_index=row_index,
            only_status=status,
            only_shop=shop,
            skip_done=skip_done,
        )
    )

    if not rows:
        console.print("[yellow]没有匹配的数据行[/yellow]")
        raise typer.Exit(1)

    payload: list[dict[str, Any]] = []
    for row in rows:
        view = row.to_view()
        view["images"] = {k: v for k, v in row.images.items()}
        if check_files:
            view["missing_files"] = iter_missing_files(row)
        payload.append(view)

    if as_json:
        console.print_json(json.dumps(payload, ensure_ascii=False))
        return

    t = Table(title=f"{cfg.project.table.name}｜{len(rows)} 行", header_style="bold", show_lines=True)
    for col in (
        "行", "row_key", "店铺", "模板SPU", "主图", "素材图",
        "SKU货号", "中文标题", "英文标题", "状态",
    ):
        t.add_column(col, overflow="fold")

    for row in rows:
        t.add_row(
            str(row.row_index),
            row.row_key,
            row.shop,
            row.template_spu,
            row.main_image,
            row.material_image,
            row.sku_prefix_code or "[red]缺[/red]",
            row.title_cn,
            row.title_en,
            row.status or "-",
        )
    console.print(t)

    if check_files:
        for row in rows:
            missing = iter_missing_files(row)
            if missing:
                console.print(f"[yellow]第 {row.row_index} 行缺图：{missing}[/yellow]")
            else:
                console.print(f"[green]第 {row.row_index} 行图片齐备[/green]")


# ------------------------------------------------------------------ 浏览器 / 探测
def _make_provider(cfg: Config) -> PlaywrightLocalProvider:
    name = cfg.browser.provider
    if name == "playwright_local":
        return PlaywrightLocalProvider()
    if name == "system_chrome":
        cfg.browser.channel = cfg.browser.channel or "chrome"
        return PlaywrightLocalProvider()
    raise typer.BadParameter(f"暂不支持 browser.provider={name}（zhanfu 适配器留到后续阶段）")


@browser_app.command("selfcheck")
def browser_selfcheck(
    shop: str = typer.Option("__selfcheck__", "--shop", help="用哪个 profile（默认用独立的自检目录）"),
    headed: bool = typer.Option(False, "--headed", help="显示浏览器窗口（默认无头）"),
) -> None:
    """验证 Chromium 能启动、能开页面、能抽取元素清单——不碰真实站点。"""
    cfg = _boot()
    cfg.browser.headless = not headed
    shop_cfg = ShopCfg(name=shop, profile_dir=cfg.root / "profiles" / "__selfcheck__")

    console.print(f"使用的浏览器：{describe_one_line(cfg)}")

    try:
        with _make_provider(cfg).launch(cfg, shop_cfg) as session:
            page = session.page()
            # 用 set_content 而不是 data: URL —— 后者会把中文做 URL 编码，导致文案比对失效
            page.set_content(
                "<h1>temu-bot selfcheck</h1><button>保存</button>"
                "<input placeholder='标题' /><a href='/x'>链接</a>"
            )
            data = collect(page)
    except BrowserError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    for line in summarize(data, limit=10):
        console.print(line)

    found = {el.get("text") or el.get("placeholder") or el.get("href") for el in data.get("elements", [])}
    ok = "保存" in found
    console.print("[green]浏览器层正常[/green]" if ok else "[red]浏览器起来了，但没抽到预期元素[/red]")
    if not ok:
        raise typer.Exit(1)


@app.command()
def login(
    shop: str = typer.Option(
        ...,
        "--shop",
        help="登录身份（profiles/<这个>；不要求在 config.shops 里登记过）",
    ),
    url: str | None = typer.Option(None, "--url", help="起始地址，默认取 config/selectors/temu.yaml 的 urls.start"),
    until_closed: bool = typer.Option(
        False,
        "--until-closed",
        help="等到你**把浏览器窗口关掉**为止（边等边存登录态）。控制台里点『打开店小秘』走的就是这个——"
        "子进程没有终端可以按回车。",
    ),
    hold_minutes: int = typer.Option(
        30, "--hold-minutes", help="--until-closed 最长等多少分钟（到点自动收工）"
    ),
) -> None:
    """打开浏览器让你**人工登录一次**（登录态存在该店铺的 profile 里，之后一直复用）。

    刻意不靠 URL 猜测"登录成功"——猜错会直接跳过你的登录步骤。

    默认以你按回车为准；`--until-closed` 则改为**以关掉浏览器窗口为准**，
    期间每隔几秒存一次登录态（cookie 只在浏览器还开着的时候读得到，
    所以必须边等边存，不能等关了再存）。
    """
    cfg = _boot()
    # 默认打开**店小秘**（上架走的是它，不是 Temu 卖家后台）。
    # 选择器配置缺失时也必须回退到店小秘——打包版没带 config/selectors/ 时就是这种情况。
    start = login_start_url(cfg, url)
    shop_cfg = cfg.shop_or_default(shop)

    console.print(f"[bold]店铺[/bold] {shop}    [bold]profile[/bold] {shop_cfg.profile_dir}")
    console.print(f"[bold]打开[/bold] {start}")
    console.print(
        "[yellow]请在浏览器窗口里登录**店小秘**（dianxiaomi.com）；"
        "地址不对就直接在地址栏输入店小秘地址。[/yellow]"
    )

    try:
        with _make_provider(cfg).launch(cfg, shop_cfg, start_url=start) as session:
            if until_closed:
                shop_session = _hold_until_closed(cfg, shop, session, hold_minutes=hold_minutes)
                console.print("[green]浏览器已关闭[/green]")
            else:
                _ask("[cyan]登录完成（能看到店小秘首页）后，在这里按回车继续 › [/cyan]")
                logged_in = session.is_logged_in()  # 记进快照，便于排查
                paths = dump_page(
                    cfg,
                    session.page(),
                    "login-after",
                    shop=shop,
                    extra={"logged_in": logged_in, "start_url": start},
                )
                # 关键：把 cookie/localStorage 导出给接口客户端用
                shop_session = save_session_from_context(cfg, shop, session.context())
    except BrowserError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    console.print(f"[green]已保存登录态[/green]（profile：{shop_cfg.profile_dir}）")

    console.print(
        f"接口用的会话文件：{session_path(cfg, shop)}"
        f"（{len(shop_session.cookies)} 个 cookie：{'、'.join(shop_session.cookie_names()[:8])}…）"
    )
    console.print(f"快照：{paths['json']}")

    # 关键：当场用这份登录态调一次接口，别让"没登录"被当成"已保存"
    try:
        with DxmClient(cfg, shop, session=shop_session) as client:
            result = client.health_check()
    except (NotLoggedIn, DxmError) as exc:
        result = {"ok": False, "reason": "接口调用失败", "detail": str(exc)[:200]}

    if result["ok"]:
        console.print(f"[green]接口验证通过[/green]｜userIn 字段：{result['data_keys']}")
        console.print("下一步：双击「2-检查接口.bat」核对店铺，然后「7-干跑上架一行.bat」试跑。")
    else:
        console.print(f"[red]接口验证没通过[/red]：{result['reason']}｜{result['detail']}")
        if result.get("sample"):
            console.print(f"接口返回结构（便于排查）：{result['sample']}")
        console.print(
            "[yellow]说明这次没有真正登录成功。请重新双击本步骤，"
            "在浏览器里**登录成功**（能看到店小秘首页/商品页）之后再按回车。[/yellow]"
        )
        raise typer.Exit(1)
    console.print(
        f"下一步：temu-bot dxm shops --shop {shop}  —— 看一眼这个账号里到底有哪几家店"
    )


@app.command()
def probe(
    shop: str = typer.Option(..., "--shop", help="店铺名"),
    url: str | None = typer.Option(None, "--url", help="要抓的地址（交互模式可省略）"),
    label: str = typer.Option("page", "--label", help="快照文件名前缀"),
    interactive: bool = typer.Option(False, "--interactive", "-i", help="在浏览器里自由导航，按回车逐个抓取"),
    settle_s: float = typer.Option(3.0, "--settle", help="打开后等页面渲染几秒再抓"),
    full: bool = typer.Option(False, "--full", help="打印全部元素明细（默认前 40 个）"),
    headed: bool = typer.Option(True, "--headed/--headless", help="是否显示浏览器窗口"),
    net: bool = typer.Option(True, "--net/--no-net", help="同时录接口请求、弹窗、控制台报错、失败请求"),
) -> None:
    """抓取页面结构化快照：元素清单 + 标题 + HTML + 截图，存到 data/reports/probe/。

    开了 --net（默认开）还会记录接口请求与**页面弹窗**——"点了没反应"多半是弹窗被
    静默取消，或前端 JS 报错，或请求失败；这里都会记下来并且把弹窗点掉。
    """
    cfg = _boot()
    cfg.browser.headless = not headed
    shop_cfg = cfg.shop_or_default(shop)

    from temu_bot.browser.recorder import Recorder

    recorder: Recorder | None = None
    try:
        with _make_provider(cfg).launch(cfg, shop_cfg) as session:
            if net:
                recorder = Recorder(cfg).attach(session.context())
                console.print("[green]已开启录制[/green]：接口请求 / 页面弹窗（会自动点确定）/ JS 报错 / 失败请求")

            if url:
                session.open(url)
                time.sleep(max(settle_s, 0))
                paths = dump_page(cfg, session.page(), label, shop=shop)
                for line in summarize(collect(session.page()), limit=1000 if full else 40):
                    console.print(line)
                console.print(f"[green]快照[/green] {paths['json']}")
                return

            if not interactive:
                console.print("[yellow]要么给 --url，要么加 --interactive[/yellow]")
                raise typer.Exit(2)

            console.print("[bold]交互抓取[/bold]：在浏览器里复现问题（比如点那个打不开的编辑），")
            console.print("回到这里按回车抓取；输入 q 回车结束。")
            console.print("[dim]会把当前打开的每个标签页都抓一遍（编辑页常是新开标签）。[/dim]")
            index = 0
            while True:
                answer = _ask("[cyan]回车=抓取所有标签页 / q=结束 › [/cyan]").strip().lower()
                if answer in {"q", "quit", "exit"}:
                    break
                index += 1

                pages = session.pages()
                if not pages:
                    console.print("[yellow]当前没有打开的标签页[/yellow]")
                    continue

                console.print(f"  共 {len(pages)} 个标签页：")
                for tab_index, page in enumerate(pages, start=1):
                    try:
                        url = page.url
                    except Exception:  # noqa: BLE001 - 标签可能刚好被关掉
                        url = "(已关闭)"
                    console.print(f"    [{tab_index}] {url}")

                    if not url or url == "about:blank":
                        console.print("        [dim]空白页，跳过[/dim]")
                        continue
                    paths = dump_page(
                        cfg, page, f"{label}-{index:02d}-tab{tab_index}", shop=shop
                    )
                    console.print(f"        → {paths['json']}")
    except BrowserError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    finally:
        if recorder is not None:
            summary = recorder.summary()
            recorder.close()
            console.print("\n[bold]本次录制摘要[/bold]")
            console.print(f"  接口请求 {summary['requests']} 条")
            if summary["dialogs"]:
                console.print(f"  [yellow]页面弹窗 {len(summary['dialogs'])} 个（已自动点确定）：[/yellow]")
                for item in summary["dialogs"][:10]:
                    console.print(f"    - {item}")
            if summary["console_errors"]:
                console.print(f"  [yellow]JS 报错/警告 {len(summary['console_errors'])} 条：[/yellow]")
                for item in summary["console_errors"][:5]:
                    console.print(f"    - {item}")
            if summary["failed_requests"]:
                console.print(f"  [yellow]失败请求 {len(summary['failed_requests'])} 条：[/yellow]")
                for item in summary["failed_requests"][:5]:
                    console.print(f"    - {item}")
            console.print(f"  明细：{summary['jsonl']}")


@app.command()
def record(
    shop: str = typer.Option(..., "--shop", help="店铺名"),
    url: str | None = typer.Option(None, "--url", help="起始地址，默认店小秘首页"),
    max_body: int = typer.Option(2_000_000, "--max-body", help="单个响应体最大记录字节数"),
    goto: str | None = typer.Option(None, "--goto", help="录制前直接打开这个地址"),
    edit_id: str | None = typer.Option(None, "--edit-id", help="直接打开该商品的编辑页（绕过点击）"),
    pick: int = typer.Option(
        0, "--pick", help="自动挑「待发布」里第 N 条商品，直接打开它的编辑页（绕过点击）"
    ),
) -> None:
    """录制店小秘接口流量：你手动走一遍上架流程，我把真实请求/响应记下来。

    这是写 API 客户端的依据——比猜页面选择器靠谱得多。
    cookie/token 的值不会落盘，只记录"有哪些头、有哪些 cookie 名"。
    """
    cfg = _boot()
    start = login_start_url(cfg, url)
    shop_cfg = cfg.shop_or_default(shop)

    from temu_bot.browser.recorder import Recorder

    recorder: Recorder | None = None
    captured: dict[str, Any] = {"text": None, "files": []}
    try:
        with _make_provider(cfg).launch(cfg, shop_cfg, start_url=start) as session:
            context = getattr(session, "_ctx", None)
            if context is None:
                console.print("[red]当前 provider 不支持网络录制[/red]")
                raise typer.Exit(1)

            recorder = Recorder(cfg, max_body=max_body).attach(context)

            # 直接跳到编辑页：店小秘点「编辑」会开一个空白新标签（自动化浏览器里进不去），
            # 但编辑页地址是确定的，直接 goto 就绕过去了。
            target = goto
            if not target and edit_id:
                target = f"{DXM_BASE}/web/popTemu/edit?id={edit_id}"
            if not target and pick > 0:
                with DxmClient(cfg, shop) as client:
                    for index, row in enumerate(
                        client.iter_products(dxm_state="offline", page_size=50), start=1
                    ):
                        if index == pick:
                            product_id = str(row.get("id") or "")
                            target = f"{DXM_BASE}/web/popTemu/edit?id={product_id}"
                            console.print(
                                f"自动挑中第 {pick} 条：{str(row.get('productName'))[:40]}"
                                f"（id={product_id}，状态={row.get('dxmOfflineState')}）"
                            )
                            break
                if not target:
                    console.print(f"[yellow]待发布里没有第 {pick} 条，改为只开首页[/yellow]")

            if target:
                console.print(f"[bold]直接打开[/bold] {target}")
                session.open(target)
                try:  # 等 SPA 把数据拉完，否则录不到编辑页的接口
                    session.page().wait_for_load_state("networkidle", timeout=30_000)
                except Exception:  # noqa: BLE001 - 页面一直有轮询时 networkidle 会超时，忽略
                    pass
                time.sleep(3)
            else:
                console.print(
                    "[dim]提示：若点「编辑」开出来是空白页，加 --pick 1 直接跳编辑页。[/dim]"
                )

            console.print("[bold green]录制已开始[/bold green] —— 请**只在刚弹出的那个浏览器窗口**里操作！")
            console.print(
                "[yellow]怎么认：工具打开的 Chrome 顶部有一条黄色警告条"
                "（写着 --no-sandbox）；你自己平时的 Chrome 没有这条。[/yellow]"
            )
            console.print("操作流程见启动器窗口里的提示（改标题 / 换图 / 保存 等）。")

            stop = threading.Event()
            _watch_requests(recorder, stop)
            try:
                _ask("[cyan]整段流程走完后，回到这里按回车结束录制 › [/cyan]")
            finally:
                stop.set()

            # 页面侧捕获的请求体（multipart 只能这样拿）
            try:
                captured = recorder.dump_page_captures(session.pages())
            except Exception as exc:  # noqa: BLE001
                captured = {"text": None, "files": []}
                console.print(f"[yellow]页面侧请求体导出失败：{exc}[/yellow]")

            summary = recorder.summary()
    except BrowserError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    finally:
        if recorder is not None:
            recorder.close()

    console.print_json(json.dumps(summary, ensure_ascii=False))
    console.print(
        f"\n[bold]请求明细[/bold]：{summary['jsonl']}\n[bold]响应体[/bold]：{summary['bodies']}"
    )
    if captured.get("text"):
        console.print(f"[bold green]请求体（页面侧捕获）[/bold green]：{captured['text']}")
    if captured.get("files"):
        console.print("[bold green]上传的文件内容（已 base64 还原，zip 已解包）[/bold green]：")
        for item in captured["files"]:
            console.print(f"  {item}")
    if not captured.get("text") and not captured.get("files"):
        console.print(
            "[yellow]没捕获到请求体[/yellow]——如果这次操作里有「保存」，说明保存请求没发出"
            "（多半是按回车太早或没点保存）。"
        )
    if summary["requests"] == 0:
        console.print("[yellow]一条都没录到——确认录制期间确实在店小秘页面上操作了。[/yellow]")
        raise typer.Exit(1)


# ------------------------------------------------------------------ 登录态
@session_app.command("show")
def session_show(
    shop: str = typer.Option(..., "--shop", help="店铺名"),
) -> None:
    """查看某店铺的登录态文件（只显示 cookie 名字，不显示值）。"""
    cfg = _boot()
    path = session_path(cfg, shop)
    session = load_session(cfg, shop)

    if session is None:
        console.print(f"[yellow]没有登录态：{path}[/yellow]")
        console.print(f'先跑：temu-bot login --shop "{shop}"')
        raise typer.Exit(1)

    t = Table(title=f"{shop} 登录态", header_style="bold")
    t.add_column("项", style="cyan", no_wrap=True)
    t.add_column("值", overflow="fold")
    t.add_row("文件", str(path))
    t.add_row("导出时间", session.exported_at or "（未知）")
    t.add_row("cookie 数", str(len(session.cookies)))
    t.add_row("cookie 名", "、".join(session.cookie_names()) or "（无）")
    t.add_row("已过期?", "是（需要重新登录）" if session.looks_expired() else "否")
    t.add_row("记录的头", "、".join(sorted(session.headers)) or "（无）")
    t.add_row("localStorage", "有" if path.with_name(path.stem + "_localstorage.json").exists() else "无")
    console.print(t)


@session_app.command("check")
def session_check(
    shop: str = typer.Option(..., "--shop", help="店铺名"),
) -> None:
    """用登录态直调店小秘接口探活——证明"浏览器登录 → 接口操作"这条路通。"""
    cfg = _boot()
    try:
        with DxmClient(cfg, shop) as client:
            result = client.health_check()
    except NotLoggedIn as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except DxmError as exc:
        console.print(f"[red]接口异常：{exc}[/red]")
        raise typer.Exit(1) from exc

    if result["ok"]:
        console.print(f"[green]接口可用[/green]｜userIn 返回字段：{result['data_keys']}")
    else:
        console.print(f"[red]{result['reason']}[/red]：{result['detail']}")
        raise typer.Exit(1)


# ------------------------------------------------------------------ 店小秘接口
def _dxm(shop: str) -> DxmClient:
    cfg = _boot()
    try:
        return DxmClient(cfg, shop)
    except NotLoggedIn as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@dxm_app.command("balance")
def dxm_balance(shop: str = typer.Option(..., "--shop")) -> None:
    """查米豆余额（按次计费服务的额度）。

    **复制商品实测不扣这里的额度**——别把它当成上架的前置成本。
    """
    client = _dxm(shop)
    try:
        data = client.midou_balance()
    finally:
        client.close()

    view = data.get("viewMap") or {}
    t = Table(title=f"米豆余额（{shop}）", header_style="bold")
    t.add_column("服务", style="cyan")
    t.add_column("单价", justify="right")
    t.add_column("可用次数", justify="right")
    for key, item in sorted(view.items()):
        t.add_row(f"服务{key}", str(item.get("unitCost")), str(item.get("availableCount")))
    console.print(t)
    console.print(
        f"可用余额 {data.get('availableBalance')}｜今日赠送 {data.get('todayFreeBalance')}"
    )


@dxm_app.command("shops")
def dxm_shops(shop: str = typer.Option(..., "--shop")) -> None:
    """列出账号下的店铺（店铺名 ↔ 店小秘 shopId）。"""
    client = _dxm(shop)
    try:
        shops = client.shops()
    finally:
        client.close()
    console.print_json(json.dumps(shops[:30], ensure_ascii=False))


@dxm_app.command("retitle")
def dxm_retitle(
    shop: str = typer.Option(..., "--shop"),
    product_id: str = typer.Option(..., "--id", help="店小秘商品id"),
    save: bool = typer.Option(False, "--save", help="真保存（默认只显示将要改什么）"),
    op: int = typer.Option(1, "--op", help="1=保存 2=立即发布 3=保存并移入待发布"),
) -> None:
    """清洗英文标题（去掉 U+2010 这类"隐形异体字符"），可选保存。

    默认 **dry-run**：只显示会改什么，不写你的数据。加了 --save 才真提交。
    """
    from temu_bot.product import bad_chars, fix_en_title, get_en_title

    client = _dxm(shop)
    try:
        response = client.product_edit(product_id)
        product = (response.get("data") or {}).get("product") or {}
        if not product:
            console.print("[red]没读到商品对象（id 不对或不属于该账号）[/red]")
            raise typer.Exit(1)

        before_cn = str(product.get("productName") or "")
        before_en = get_en_title(product)
        _, after_en = fix_en_title(product)
        changed = after_en != before_en

        t = Table(title=f"商品 {product_id}｜状态 {product.get('dxmState')}", header_style="bold")
        t.add_column("项", style="cyan", no_wrap=True)
        t.add_column("内容", overflow="fold")
        t.add_row("中文标题", before_cn[:70])
        t.add_row("英文标题·改前", before_en[:110])
        t.add_row("发现异体字符", " ".join(f"{c}(U+{ord(c):04X})" for c in bad_chars(before_en)) or "无")
        t.add_row("英文标题·改后", after_en[:110])
        console.print(t)

        if not changed:
            console.print("[yellow]英文标题本来就没有异体字符，无需修改。[/yellow]")
            return

        if not save:
            console.print("[yellow]dry-run：未提交。确认要保存就加 --save。[/yellow]")
            return

        # 载荷由 save_product 内部按抓包校准的 39 字段表生成，这里只要传商品对象
        result = client.save_product(product, op=op)
    finally:
        client.close()

    code = (result or {}).get("code")
    if code == 0:
        console.print(f"[green]保存成功[/green]（op={op}）｜返回 data={str(result.get('data'))[:80]}")
    else:
        console.print(f"[red]保存失败[/red] code={code} msg={(result or {}).get('msg')}")
        raise typer.Exit(1)


@dxm_app.command("failures")
def dxm_failures(
    shop: str = typer.Option(..., "--shop"),
    limit: int = typer.Option(500, "--limit", help="最多扫描多少条发布失败"),
    json_out: str = typer.Option("", "--json-out", help="把明细导出成 JSON 文件"),
) -> None:
    """把「发布失败」按原因分类，并标出每类该怎么处置（只读）。"""
    from temu_bot.dianxiaomi.failures import build_triage

    client = _dxm(shop)
    try:
        rows = list(
            client.iter_products(
                dxm_state="offline", dxmOfflineState="publishFail", page_size=50, max_items=limit
            )
        )
    finally:
        client.close()

    triage = build_triage(rows)
    groups = triage.by_category()

    t = Table(title=f"发布失败分类（共 {len(triage.failures)} 条）", header_style="bold")
    t.add_column("分类", style="cyan", overflow="fold")
    t.add_column("条数", justify="right")
    t.add_column("处置", no_wrap=True)
    t.add_column("说明", overflow="fold")

    # 新加的处置方式忘了配色会直接 KeyError，用默认值兜住
    fix_color = {
        "自动修标题": "green",
        "自动修图": "green",
        "自动重试": "green",
        "等额度": "yellow",
        "需重新授权": "red",
        "需人工": "red",
    }

    order = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    for code, items in order:
        category = items[0].category
        color = fix_color.get(category.fix.value, "white")
        t.add_row(category.label, str(len(items)), f"[{color}]{category.fix.value}[/]", category.advice)
    console.print(t)

    console.print("\n[bold]样例[/bold]")
    for code, items in order:
        first = items[0]
        console.print(f"  · {first.category.label}：{first.title[:34]}  (id={first.product_id})")
        console.print(f"    原始 errMsg：{first.err_msg[:90]}")

    summary = triage.counts_by_fix()
    console.print("\n[bold]按处置方式汇总[/bold]：" + "、".join(f"{k} {v} 条" for k, v in summary.items()))

    if json_out:
        path = Path(json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                [
                    {
                        "product_id": f.product_id,
                        "title": f.title,
                        "err_msg": f.err_msg,
                        "category": f.category.code,
                        "label": f.category.label,
                        "fix": f.category.fix.value,
                    }
                    for f in triage.failures
                ],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        console.print(f"明细已导出：{path}")


@dxm_app.command("check")
def dxm_check(
    shop: str = typer.Option(..., "--shop", help="用哪个登录态（登录身份 key）"),
    expect: str = typer.Option(
        ..., "--expect", help="表格里这行的「店铺名」，多个用逗号分隔"
    ),
) -> None:
    """核对当前登录的店小秘账号里有没有表里要上的那个店——不一致就拦下来。"""
    client = _dxm(shop)
    try:
        wanted = [x.strip() for x in expect.split(",") if x.strip()]
        result = client.verify_shop(wanted)
    finally:
        client.close()

    if result.ok:
        console.print(f"[green]✅ {result.message()}[/green]")
        return

    console.print(f"[red]❌ {result.message()}[/red]")
    console.print("先用 [bold]dxm shops --shop <登录身份>[/bold] 看看当前账号里有哪些店。")
    raise typer.Exit(1)


@dxm_app.command("list")
def dxm_list(
    shop: str = typer.Option(..., "--shop"),
    state: str = typer.Option("offline", "--state", help="offline 待发布 / online 在线 / draft 草稿"),
    limit: int = typer.Option(10, "--limit", "-n"),
    search: str = typer.Option("", "--search", help="按标题搜索"),
) -> None:
    """列商品（只读）。"""
    client = _dxm(shop)
    try:
        data = client.list_products(
            dxm_state=state, page_size=min(max(limit, 1), 50), search_value=search,
            search_type=1 if search else 0,
        )
    finally:
        client.close()

    page = data.get("page") or {}
    rows = page.get("list") or []
    t = Table(title=f"{state} 商品｜共 {page.get('totalSize')} 条", header_style="bold")
    for col in ("商品id", "中文标题", "店铺id", "类目", "更新时间"):
        t.add_column(col, overflow="fold")
    for row in rows[:limit]:
        t.add_row(
            str(row.get("id")),
            str(row.get("productName") or "")[:40],
            str(row.get("shopId")),
            str(row.get("categoryPathName") or "")[:28],
            str(row.get("updateTime") or ""),
        )
    console.print(t)


@dxm_app.command("show")
def dxm_show(
    shop: str = typer.Option(..., "--shop"),
    product_id: str = typer.Option(..., "--id"),
) -> None:
    """看单个商品的完整结构（只读，用于对字段）。"""
    client = _dxm(shop)
    try:
        data = client.product_edit(product_id)
    finally:
        client.close()
    detail = (data or {}).get("data") or {}
    console.print(f"字段数 {len(detail)}")
    console.print_json(json.dumps({k: detail[k] for k in list(detail)[:40]}, ensure_ascii=False))


@dxm_app.command("copy")
def dxm_copy(
    shop: str = typer.Option(..., "--shop", help="用哪个店铺的登录态"),
    product_id: str = typer.Option(..., "--product", help="被复制的模板商品id"),
    target_shop: str = typer.Option(..., "--target", help="目标店铺名（会换算成 shopId）"),
    yes: bool = typer.Option(False, "--yes", help="真发请求（不加只打印将要发送的内容）"),
) -> None:
    """复制商品（搬家）。默认只做 dry-run。

    实测**不扣米豆**（复制前后 `balanceView` 所有桶都没变）；要防的是重复铺货。
    """
    client = _dxm(shop)
    try:
        shop_id = client.find_shop_id(target_shop)
        if not shop_id:
            console.print(f"[red]找不到店铺「{target_shop}」，先跑 dxm shops 看看[/red]")
            raise typer.Exit(1)

        payload = {
            "shopIds": shop_id,
            "productId": product_id,
            "platform": "popTemu",
            "hidePrice": "0",
        }
        console.print("[bold]将发送[/bold] POST /api/banjia/copyProduct.json")
        console.print_json(json.dumps(payload, ensure_ascii=False))

        if not yes:
            balance = client.midou_balance()
            view = balance.get("viewMap") or {}
            console.print(
                "[yellow]dry-run：未发送。[/yellow]米豆额度（实测复制不扣，仅供参考）："
                + "、".join(f"服务{k} 剩 {v.get('availableCount')}" for k, v in sorted(view.items()))
            )
            console.print("确认要执行就加 [bold]--yes[/bold]。")
            return

        result = client.copy_product(product_id, shop_id)
    finally:
        client.close()

    console.print_json(json.dumps(result, ensure_ascii=False)[:800])


@dxm_app.command("publish")
def dxm_publish(
    shop: str = typer.Option(..., "--shop"),
    ids: str = typer.Option(..., "--ids", help="商品id，多个用逗号分隔"),
    path: str = typer.Option("", "--path", help="覆盖发布接口路径（默认 /api/product/popTemu/batchPublish.json）"),
    yes: bool = typer.Option(False, "--yes", help="真发请求"),
) -> None:
    """发布商品——**真实上线**，默认只做 dry-run。"""
    target = path or "/api/product/popTemu/batchPublish.json"
    console.print(f"[bold]将发送[/bold] POST {target}")
    console.print_json(json.dumps({"ids": ids}, ensure_ascii=False))
    if not yes:
        console.print("[yellow]dry-run：未发送。[/yellow]确认要发布就加 [bold]--yes[/bold]。")
        return

    client = _dxm(shop)
    try:
        result = client.batch_publish(ids, path=path or None)
    finally:
        client.close()
    console.print_json(json.dumps(result, ensure_ascii=False)[:800])


@app.command()
def run(
    shop: str = typer.Option(
        "",
        "--shop",
        help="登录身份；**一般不用给**——不给就按表格里的店铺名自动认用哪个身份",
    ),
    row_key: str | None = typer.Option(None, "--row-key", help="按幂等键指定一行"),
    row_index: int | None = typer.Option(None, "--row-index", help="按 Excel 行号指定一行"),
    live: bool = typer.Option(False, "--live", help="真执行（会复制建商品、传图、写数据）"),
    op: int = typer.Option(1, "--op", help="保存后的动作：1=仅保存 2=立即发布 3=移入待发布"),
    images_dir: str = typer.Option("", "--images-dir", help="图片文件夹；给了就覆盖表里第 1 列"),
    swap_images: bool | None = typer.Option(
        None,
        "--swap-images/--no-swap-images",
        help="表里已有商品ID时也强制换图（默认不换：已有商品是重试/续跑，重复传图没意义）",
    ),
    json_out: str = typer.Option("", "--json-out", help="把结果写成 JSON"),
) -> None:
    """上架一行商品（**默认干跑**：只跑检查步骤，不碰你的数据）。

    只需要给**信息表格**；图片位置由表里给（第 1 列是文件夹，各图片列是文件名）。
    --images-dir 是可选覆盖项，平时不用给。
    流程：PRECHECK → MEDIA(找图并出图) → SHOP_CHECK(核对店铺) → FIND_TEMPLATE
          → COPY → FIND_NEW → UPLOAD(传图) → APPLY(换图 + 改标题保存)
          → WRITEBACK(商品ID/状态写回表格)

    最后一步 WRITEBACK 是**防重复铺货的闸门**：表里没有商品ID 的行，下次跑还是"新建"。
    表开着 Excel 时写不回去，那一步会 ❌ 并给出补写命令（`table writeback --live`），
    但**不算上架失败**——商品已经建好了。
    """
    _run_one(shop, row_key, row_index, live, op, json_out, images_dir, swap_images)


def _run_one(
    shop: str,
    row_key: str | None,
    row_index: int | None,
    live: bool,
    op: int,
    json_out: str,
    images_dir: str = "",
    swap_images: bool | None = None,
) -> None:
    from temu_bot.listing import ListingPipeline

    cfg = _boot()
    table = SheetTable.from_config(cfg)

    rows = list(table.iter_rows(limit=1, row_key=row_key, row_index=row_index))
    if not rows:
        console.print("[yellow]没找到指定的行（用 table read 看看有哪些行）[/yellow]")
        raise typer.Exit(1)
    row = rows[0]

    console.print(
        f"[bold]第 {row.row_index} 行[/bold]｜店铺 {row.shop}｜主图 {row.main_image}｜"
        f"模板SPU {row.template_spu}"
    )
    console.print(
        f"登录身份：{'「' + shop + '」（指定的）' if shop else '**按表格店铺名自动认**'}"
    )
    console.print(f"模式：{'[red]真实执行[/red]' if live else '[green]干跑（不写数据）[/green]'}\n")

    outcome = ListingPipeline(
        cfg,
        row,
        login=shop,
        live=live,
        op=op,
        images_dir=Path(images_dir) if images_dir else None,
        swap_images=swap_images,
    ).run()

    for step in outcome.steps:
        style = {
            "ok": "green",
            "dry": "cyan",
            "skip": "dim",
            "fail": "red",
        }[step.status.value]
        console.print(f"[{style}]{step.line()}[/{style}]")

    if live:
        # 记一笔账：万一回写表格失败，靠这个日志能补写（temu-bot table writeback）
        _append_run_log(cfg, row, outcome, login=shop, op=op)

    if json_out:
        path = Path(json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(outcome.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        console.print(f"\n结果已导出：{path}")

    if outcome.failed:
        raise typer.Exit(1)
    if outcome.writeback_failed:
        console.print(
            "\n[yellow]注意：上架成功了，但商品ID/状态**没能写回表格**（多半是表开着 Excel）。"
            "关掉表后跑 [bold]temu-bot table writeback --live[/bold] 补上——"
            "不补的话这一行下次还会被当成「新建」，会重复铺货。[/yellow]"
        )
    if not live:
        console.print("\n[cyan]干跑结束：上面的 🔸 是真正执行时会做的动作。确认无误再加 --live。[/cyan]")


def _hold_until_closed(cfg: Config, shop: str, session, *, hold_minutes: int):
    """守着浏览器，每隔几秒存一次登录态，直到你把窗口关掉（或超时）。

    **为什么要边等边存**：cookie 只在浏览器还开着的时候读得到（`storage_state`
    要从活着的上下文里取）。等窗口关了再存就晚了——那时什么也读不到。
    """
    deadline = time.monotonic() + max(1, hold_minutes) * 60
    saved = None
    n = 0
    while True:
        try:
            saved = save_session_from_context(cfg, shop, session.context())
            n += 1
        except Exception as exc:  # noqa: BLE001 - 存不下来别把窗口带崩
            console.print(f"[yellow]这一次没存成登录态（继续等）：{exc}[/yellow]")
        if not session.is_open():
            break
        if time.monotonic() > deadline:
            console.print(f"[yellow]等够 {hold_minutes} 分钟了，自动收工。[/yellow]")
            break
        time.sleep(HOLD_SAVE_SECONDS)
    console.print(f"[dim]等待期间共保存 {n} 次登录态[/dim]")
    if saved is None:
        raise BrowserError("一直没能保存登录态，请重试")
    return saved


def _append_run_log(cfg: Config, row, outcome, *, login: str, op: int) -> None:
    """把这次真跑的结果追加进 live_runs.jsonl（回写失败时的补救依据）。"""
    path = cfg.data_dir / "state" / "live_runs.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "row_index": row.row_index,
            "row_key": row.row_key,
            "shop": row.shop,
            "login": login,
            "op": op,
            "ok": not outcome.failed,
            "product_id": outcome.product_id,
            "writeback_ok": outcome.writeback_ok,
            "steps": [s.name for s in outcome.steps],
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        console.print(f"[yellow]记账失败（不影响上架结果）：{exc}[/yellow]")


# ------------------------------------------------------------------ 上架前输入校验
@app.command()
def preflight(
    images_dir: str = typer.Option("", "--images-dir", help="图片文件夹；给了就覆盖表里第 1 列"),
    row_key: str | None = typer.Option(None, "--row-key", help="只查这一行"),
    row_index: int | None = typer.Option(None, "--row-index", help="按 Excel 行号只查一行"),
    limit: int | None = typer.Option(None, "--limit", "-n", help="只查前 N 行"),
    skip_done: bool = typer.Option(False, "--skip-done", help="跳过状态=成功的行"),
    show: int = typer.Option(20, "--show", help="最多打印几行明细"),
) -> None:
    """上架前把输入验清楚：**信息表格**，以及表里指出的图片（只读，不碰接口）。

    表里写的是图片名、第 1 列是图片所在的文件夹；图到底在不在，得去那个目录对一遍才知道。
    找不到的行会在碰接口之前就被挡下来。
    """
    from temu_bot.preflight import check_inputs

    cfg = _boot()
    report = check_inputs(
        cfg,
        images_dir=Path(images_dir) if images_dir else None,
        limit=limit,
        row_key=row_key,
        row_index=row_index,
        skip_done=skip_done,
    )

    t = Table(title="上架前输入校验", header_style="bold")
    t.add_column("输入", style="cyan", no_wrap=True)
    t.add_column("结果", no_wrap=True)
    t.add_column("详情", overflow="fold")
    t.add_row("信息表格", _ok_text(report.table_ok), report.table_detail)
    t.add_row(
        "图片文件夹",
        _ok_text(report.folder_ok),
        report.folder_detail or "(未指定)",
    )
    console.print(t)

    blocked = report.blocked
    if blocked and report.folder_ok:
        # 文件夹整体不可用时，每一行的原因都一样，逐行刷屏没有任何信息量
        console.print(f"\n[red]以下 {len(blocked)} 行会在上架前被挡下来：[/red]")
        for readiness in blocked[:show]:
            console.print(f"  [red]第 {readiness.row_index} 行[/red]｜{readiness.folder}")
            for problem in readiness.problems:
                console.print(f"    {problem}")
        if len(blocked) > show:
            console.print(f"  …还有 {len(blocked) - show} 行（用 --show 调大）")
    elif blocked:
        console.print("\n[red]先把图片文件夹解决掉，再看逐行结果。[/red]")

    console.print(f"\n{report.summary()}")

    if not report.ok:
        raise typer.Exit(1)


def _ok_text(ok: bool) -> str:
    return "[green]OK[/green]" if ok else "[red]不通过[/red]"


@app.command()
def collect(
    directory: str = typer.Option(..., "--dir", help="素材文件夹（里面是 ID.jpg + ID-素材图.jpg）"),
    shop: str = typer.Option(..., "--shop", help="店铺名（写进表里）"),
    template_spu: str = typer.Option("", "--template-spu", help="模板产品SPUID（写进表里）"),
    main_index: int = typer.Option(1, "--main-index", help="主图索引（写进表里）"),
    out: str = typer.Option("", "--out", help="输出表路径；默认写到素材文件夹里，已存在会自动改名另存"),
    img_name_as_skc: bool = typer.Option(False, "--img-name-as-skc", help="以图片名作为 SKC 货号（第 19 列）"),
    force_material: bool | None = typer.Option(
        None,
        "--force-material/--no-force-material",
        help="缺素材图时用主图生成一张（默认开；要「缺就跳过该行」用 --no-force-material）",
    ),
    recursive: bool = typer.Option(
        False, "--recursive", help="连同子文件夹一起收集（默认只看所选文件夹这一层）"
    ),
    title_list: str = typer.Option(
        "",
        "--title-list",
        help="「图片文件名清单」(.xlsx/.csv)：按 11 位 ID 把中/英文标题填进第 17/18 列",
    ),
    show: int = typer.Option(15, "--show", help="最多打印几行明细"),
    live: bool = typer.Option(False, "--live", help="真的写出表格；默认只报告不写文件"),
) -> None:
    """收集图片：把素材文件夹里的图整理成一张「商品信息表」。

    配对规则：`ID.jpg` 是主图，`ID-素材图.jpg` 是它的素材图，一对 = 一行。
    **缺素材图的行默认用主图生成一张**（`--no-force-material` 则改回"缺就跳过"）。
    产出**永远是新文件**，不覆盖任何已有表。
    """
    from temu_bot.collect import CollectOptions, scan_folder, write_table

    cfg = _boot()
    force = True if force_material is None else force_material
    options = CollectOptions(
        folder=Path(directory),
        shop=shop,
        template_spu=template_spu,
        main_image_index=main_index,
        use_img_name_as_skc=img_name_as_skc,
        force_gen_material=force,
        recursive=recursive,
        title_list=Path(title_list) if title_list else None,
    )

    result = scan_folder(options)

    t = Table(title="收集图片", header_style="bold")
    t.add_column("项目", style="cyan", no_wrap=True)
    t.add_column("详情", overflow="fold")
    t.add_row("素材文件夹", str(options.folder))
    t.add_row("图片总数", str(result.image_count))
    t.add_row("收成行数", f"[green]{len(result.rows)}[/green]")
    t.add_row("跳过", f"[yellow]{len(result.skipped)}[/yellow]" if result.skipped else "0")
    t.add_row("店铺 / 模板SPU", f"{shop or '(空)'} / {template_spu or '(空)'}")
    if result.title_list is not None:
        tl = result.title_list
        if tl.error:
            t.add_row("标题清单", f"[red]{tl.error}[/red]")
        else:
            t.add_row(
                "标题清单",
                f"{tl.path.name}｜读到 {tl.rows} 个 ID（{tl.with_en} 个带英文）｜"
                f"匹配 {result.titled_rows}/{len(result.rows)} 行"
                + ("｜英文列比 ID 列多一行，已自动对齐" if tl.shifted else ""),
            )
    console.print(t)

    if result.rows:
        console.print("\n[bold]将写入的行[/bold]")
        for row in result.rows[:show]:
            note = f"　[cyan]{row.note}[/cyan]" if row.note else ""
            console.print(f"  第{row.index}行　{row.main_name}　+　{row.material_name}{note}")
        if len(result.rows) > show:
            console.print(f"  …还有 {len(result.rows) - show} 行（用 --show 调大）")

    if result.skipped:
        console.print("\n[bold]跳过[/bold]")
        for skip in result.skipped[:show]:
            console.print(f"  [yellow]{skip.name or '(整个文件夹)'}[/yellow]：{skip.reason}")
        if len(result.skipped) > show:
            console.print(f"  …还有 {len(result.skipped) - show} 条")

    if not result.ok:
        console.print("\n[red]一张图都没收到，没东西可写。[/red]")
        raise typer.Exit(1)

    if not live:
        console.print(
            f"\n[cyan]干跑：确认无误就加 --live 写出表格"
            f"（默认路径 {Path(out) if out else options.folder / '商品信息表.xlsx'}，已存在会自动改名）。[/cyan]"
        )
        return

    written = write_table(
        result,
        Path(out) if out else None,
        generate_missing_material=force,
    )
    console.print(f"\n[green]{written.summary()}[/green]")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="只允许本机地址"),
    port: int = typer.Option(8765, "--port", help="端口"),
    no_open: bool = typer.Option(False, "--no-open", help="不要自动打开浏览器"),
) -> None:
    """打开本机网页控制台（**只在本机可用**，Ctrl+C 停止）。

    刻意不走 `_boot()`——控制台要自己装配日志（还要挂内存日志 handler 给网页看），
    复用 `_boot` 会提前把日志配好、导致后面的装配变成空操作。
    """
    from temu_bot.config import ConfigError
    from temu_bot.web.app import serve as serve_app

    try:
        serve_app(host=host, port=port, open_browser=not no_open)
    except ConfigError as exc:
        console.print(f"[red]配置有问题：{exc}[/red]")
        raise typer.Exit(2) from exc
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


# ------------------------------------------------------------------ 图片管线
@images_app.command("specs")
def images_specs() -> None:
    """打印当前用的图片规格（以及规格依据）。"""
    from temu_bot.imaging import SPECS

    t = Table(title="图片规格", header_style="bold")
    for col in ("用途", "目标尺寸", "填充方式", "体积上限", "依据"):
        t.add_column(col, overflow="fold")
    for spec in SPECS.values():
        t.add_row(
            spec.label_with_grounding,
            f"{spec.width}x{spec.height}",
            "充满裁剪(不留边)" if spec.fit == "cover" else "完整放入(补白边)",
            f"{spec.max_bytes // (1024 * 1024)}MB",
            "平台报错原文" if spec.grounded else "由现有数据反推",
        )
    console.print(t)


def _pick_row(table: SheetTable, row_key: str | None, row_index: int | None):
    rows = list(table.iter_rows(limit=1, row_key=row_key, row_index=row_index))
    if not rows:
        console.print("[yellow]没找到指定的行（用 table read 看看有哪些行）[/yellow]")
        raise typer.Exit(1)
    return rows[0]


@images_app.command("check")
def images_check(
    row_key: str | None = typer.Option(None, "--row-key", help="按幂等键指定一行"),
    row_index: int | None = typer.Option(None, "--row-index", help="按 Excel 行号指定一行"),
    images_dir: str = typer.Option("", "--images-dir", help="图片文件夹；给了就覆盖表里第 1 列"),
) -> None:
    """只读：看这一行的图能不能找到、合不合规、要重出的话会怎么做。"""
    from temu_bot.imaging import index_folder, plan_row

    cfg = _boot()
    row = _pick_row(SheetTable.from_config(cfg), row_key, row_index)

    folder = Path(images_dir) if images_dir else Path(row.folder or "")
    console.print(f"[bold]第 {row.row_index} 行[/bold]｜图片文件夹 {folder}\n")

    index = index_folder(folder) if folder else None
    planned = plan_row(cfg, row, images_dir=Path(images_dir) if images_dir else None, index=index)
    if not planned:
        console.print("[yellow]这一行没有图片列[/yellow]")
        return

    style = {"pass": "green", "cover": "yellow", "contain": "yellow"}
    for item in planned:
        console.print(f"[{style.get(item.action, 'white')}]{item.line()}[/]")

    bad = [i for i in planned if i.is_rewrite]
    if bad:
        console.print(f"\n[cyan]{len(bad)} 张需要重出。用 images prep --row-index {row.row_index} --live 落盘。[/cyan]")
    else:
        console.print("\n[green]全部已合规，无需重出。[/green]")


@images_app.command("prep")
def images_prep(
    row_key: str | None = typer.Option(None, "--row-key", help="按幂等键指定一行"),
    row_index: int | None = typer.Option(None, "--row-index", help="按 Excel 行号指定一行"),
    images_dir: str = typer.Option("", "--images-dir", help="图片文件夹；给了就覆盖表里第 1 列"),
    live: bool = typer.Option(False, "--live", help="真的写出文件（只写到 work/ 下，原图绝不动）"),
) -> None:
    """按规格出图。默认干跑；--live 才落盘到 work/<row_key>/out/。"""
    from temu_bot.imaging import process_row

    cfg = _boot()
    row = _pick_row(SheetTable.from_config(cfg), row_key, row_index)

    console.print(
        f"[bold]第 {row.row_index} 行[/bold]｜图片文件夹 {images_dir or row.folder}\n"
        f"模式：{'[red]写出文件[/red]' if live else '[green]干跑（不写文件）[/green]'}\n"
    )

    result = process_row(
        cfg,
        row,
        dry_run=not live,
        images_dir=Path(images_dir) if images_dir else None,
    )
    if not result.items:
        console.print("[yellow]这一行没有图片列[/yellow]")
        return

    for item in result.items:
        console.print(item.line())

    if result.failed:
        console.print("\n[red]有图产出不合格或缺原图，先解决再往下走。[/red]")
        raise typer.Exit(1)

    if live:
        console.print(f"\n[green]已产出 {len(result.written)} 个文件 → {result.out_dir}[/green]")
        console.print("原图未被修改（只读）。")
    else:
        console.print(f"\n[cyan]干跑：届时写到 {result.out_dir}（原图只读）。确认无误再加 --live。[/cyan]")


# ------------------------------------------------------------------ 工具箱
_STATUS_STYLE = {"ok": "green", "would": "cyan", "skipped": "dim", "failed": "red"}


def _print_report(report: Any, *, live: bool) -> None:
    """把 ToolReport 打成人看的表。"""
    if not report.items:
        console.print("[yellow]没有要处理的东西。[/yellow]")
        if report.note:
            console.print(report.note)
        return

    t = Table(header_style="bold", show_lines=False)
    t.add_column("文件", style="cyan", overflow="fold", max_width=34)
    t.add_column("动作", no_wrap=True)
    t.add_column("结果", no_wrap=True)
    t.add_column("说明", overflow="fold", max_width=48)
    for item in report.items:
        style = _STATUS_STYLE.get(item.status, "")
        t.add_row(
            item.name,
            item.action,
            f"[{style}]{item.status_label}[/{style}]",
            item.detail or (Path(item.target).name if item.target else ""),
        )
    console.print(t)
    console.print(f"\n[bold]{report.summary()}[/bold]")
    if report.outputs:
        for path in report.outputs:
            console.print(f"  [green]产出[/green] {path}")
    if not live:
        console.print("[cyan]以上是干跑预览，什么都没写。确认无误再加 --live。[/cyan]")


@toolbox_app.command("ids")
def toolbox_ids() -> None:
    """看一眼 ID 台账：在哪、有多少个号（SKU 重命名发的号永远不能重发）。"""
    from temu_bot import toolbox

    cfg = _boot()
    info = toolbox.ids.describe(cfg)
    t = Table(header_style="bold")
    t.add_column("台账", style="cyan", overflow="fold")
    t.add_column("条数", justify="right")
    t.add_column("说明")
    for row in info["files"]:
        if row.get("error"):
            t.add_row(str(row["path"]), "-", f"[red]{row['error']}[/red]")
            continue
        note = "项目自己的台账（新号写这里）" if row.get("is_project") else "旧台账（只读来去重）"
        t.add_row(str(row["path"]), str(row["count"]), note)
    console.print(t)
    console.print(f"合并去重后共 [bold]{info['merged_total']}[/bold] 个已用 ID。")


@toolbox_app.command("rename")
def toolbox_rename(
    directory: str = typer.Option(..., "--dir", help="图片文件夹"),
    mode: str = typer.Option(
        "id-only",
        "--mode",
        help="id-only 纯ID / id-prefix 加前缀 / strip-id 还原ID / roundtrip 加ID+导清单+还原",
    ),
    id_length: int = typer.Option(11, "--id-length", help="ID 位数"),
    csv_path: str = typer.Option("", "--csv", help="roundtrip 模式导出清单的位置（默认写进图片文件夹）"),
    export_format: str = typer.Option("both", "--format", help="roundtrip 的清单格式：both / csv / xlsx"),
    live: bool = typer.Option(False, "--live", help="真的改名；默认只预览"),
) -> None:
    """SKU 重命名：给图片发 11 位唯一 ID（台账保证不重发）。"""
    from temu_bot import toolbox

    cfg = _boot()
    folder = Path(directory)
    kwargs = {
        "id_length": id_length,
        "csv_path": Path(csv_path) if csv_path else None,
        "export_format": export_format,
    }
    report = (
        toolbox.rename.run(cfg, folder, mode, **kwargs)
        if live
        else toolbox.rename.plan(cfg, folder, mode, **kwargs)
    )
    _print_report(report, live=live)


@toolbox_app.command("export")
def toolbox_export(
    directory: str = typer.Option(..., "--dir", help="图片文件夹"),
    csv_path: str = typer.Option("", "--csv", help="输出路径（默认写进图片文件夹）"),
    fmt: str = typer.Option("both", "--format", help="both / csv / xlsx"),
    live: bool = typer.Option(False, "--live", help="真的写出；默认只预览"),
) -> None:
    """导出图片清单：CSV（GBK，逐字节兼容老格式）+ XLSX。"""
    from temu_bot import toolbox

    _boot()
    folder = Path(directory)
    kwargs = {"csv_path": Path(csv_path) if csv_path else None, "fmt": fmt}
    report = toolbox.listing.run(folder, **kwargs) if live else toolbox.listing.plan(folder, **kwargs)
    _print_report(report, live=live)


@toolbox_app.command("convert")
def toolbox_convert(
    directory: str = typer.Option(..., "--dir", help="图片文件夹"),
    to: str = typer.Option("png", "--to", help="目标格式：png / jpg / webp / avif"),
    quality: int = typer.Option(100, "--quality", help="质量 1-100（png 无损，这个值只表示开到最大压缩）"),
    out_dir: str = typer.Option("", "--out", help="输出目录；留空 = 跟源文件同目录"),
    recursive: bool = typer.Option(False, "--recursive", help="连子文件夹一起转"),
    overwrite: bool = typer.Option(False, "--overwrite", help="覆盖已存在的输出文件"),
    live: bool = typer.Option(False, "--live", help="真的转；默认只探测预览"),
) -> None:
    """图片格式转换（Pillow，不需要 Node）。默认不删源文件。"""
    from temu_bot import toolbox

    _boot()
    folder = Path(directory)
    kwargs = {
        "target_format": to,
        "quality": quality,
        "output_dir": Path(out_dir) if out_dir else None,
        "recursive": recursive,
        "overwrite": overwrite,
    }
    report = toolbox.convert.run(folder, **kwargs) if live else toolbox.convert.plan(folder, **kwargs)
    _print_report(report, live=live)


@toolbox_app.command("merge")
def toolbox_merge(
    directory: str = typer.Option(..., "--dir", help="表格文件夹"),
    out_file: str = typer.Option("", "--out", help="输出文件；默认 <文件夹>\\MergedExcel.xlsx"),
    add_source_row: bool = typer.Option(
        False, "--add-source-row", help="每个工作簿前插入一行来源文件名"
    ),
    live: bool = typer.Option(False, "--live", help="真的合并；默认只预览"),
) -> None:
    """Excel 合并：目录里所有 .xlsx 的全部工作表纵向拼成一个。"""
    from temu_bot import toolbox

    _boot()
    folder = Path(directory)
    kwargs = {"output_file": Path(out_file) if out_file else None, "add_source_row": add_source_row}
    report = toolbox.merge.run(folder, **kwargs) if live else toolbox.merge.plan(folder, **kwargs)
    _print_report(report, live=live)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
