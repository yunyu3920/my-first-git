"""店小秘接口契约自检：改版后一眼看出是哪一步变了。

用户 2026-10-18：店小秘改版后程序要能应变。这个项目不点页面，全走 `/api/*.json`，
所以「界面改了」通常不影响；真正会坏的是**接口路径/返回结构**。这里用几个
**只读**接口做体检：

| 检查 | 接口 | 断言 |
|---|---|---|
| 登录态 | `userIn` | `data.shopMap` 能解析出店铺 |
| 商品列表 | `pageList` | `data.page.list` 存在 |
| 图片空间 | `album/index` | `albumUserSummary` 存在 |
| 商品详情（深检） | `edit?id=` | `data.product` 存在 |

结果会写成 `data/reports/diagnose/<时间戳>.json`，给 AI 排障用。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from temu_bot.config import Config
from temu_bot.dianxiaomi.client import (
    DxmClient,
    DxmError,
    NotLoggedIn,
    SiteChanged,
    is_real_shop,
)

log = logging.getLogger(__name__)


@dataclass(slots=True)
class ApiCheck:
    name: str
    ok: bool
    detail: str
    endpoint: str = ""
    # 没做（比如没有商品可深检）——不算失败
    skipped: bool = False


@dataclass(slots=True)
class ApiReport:
    login: str
    checks: list[ApiCheck] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and all(c.ok for c in self.checks)

    def failures(self) -> list[ApiCheck]:
        return [c for c in self.checks if not c.ok]

    def as_dict(self) -> dict[str, Any]:
        return {
            "login": self.login,
            "ok": self.ok,
            "error": self.error,
            "checked_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "checks": [asdict(c) for c in self.checks],
        }


def _first_endpoint(client: DxmClient, op: str) -> str:
    paths = client.endpoints.paths(op)
    return paths[0] if paths else ""


def check_api(
    cfg: Config,
    login: str,
    *,
    deep: bool = False,
    client_factory: Callable[..., Any] | None = None,
) -> ApiReport:
    """只读体检。**不写任何店小秘数据。**"""
    report = ApiReport(login=login)
    factory = client_factory or DxmClient

    try:
        with factory(cfg, login) as client:
            # 先判"登录态还活着吗"——否则 code=2001 会被误报成"结构变了"
            health = client.health_check()
            if not health.get("ok"):
                detail = str(health.get("detail") or "").strip()
                report.checks.append(
                    ApiCheck(
                        "登录态 userIn",
                        False,
                        f"{health.get('reason') or '登录态不可用'}"
                        + (f"：{detail}" if detail else ""),
                        _first_endpoint(client, "user_in"),
                    )
                )
                report.error = "登录态不可用——先重新登录（1-登录店小秘.bat）再自检"
                return report

            _check_user_in(client, report)
            first_product_id = _check_product_list(client, report)
            _check_album(client, report)
            if deep:
                _check_product_edit(client, report, first_product_id)
    except NotLoggedIn as exc:
        report.error = f"没有可用登录态：{exc}"
    except Exception as exc:  # noqa: BLE001 - 自检本身不该抛
        report.error = f"自检失败：{exc}"
        log.exception("接口自检失败")

    return report


def _check_user_in(client: DxmClient, report: ApiReport) -> None:
    endpoint = _first_endpoint(client, "user_in")
    try:
        data = client.user_in()
    except Exception as exc:  # noqa: BLE001
        report.checks.append(ApiCheck("登录态 userIn", False, _exc_text(exc), endpoint))
        return

    payload = (data or {}).get("data") or {}
    shop_map = payload.get("shopMap")
    if not isinstance(shop_map, dict):
        report.checks.append(
            ApiCheck("登录态 userIn", False, "返回里没有 data.shopMap（结构可能变了）", endpoint)
        )
        return

    shops = [s for s in client.shops() if is_real_shop(s)]
    names = "、".join(str(s.get("name")) for s in shops) or "（账号里没有真店铺）"
    report.checks.append(ApiCheck("登录态 userIn", True, f"账号里的店：{names}", endpoint))


def _check_product_list(client: DxmClient, report: ApiReport) -> str:
    endpoint = _first_endpoint(client, "product_list")
    try:
        data = client.list_products(page_size=5)
    except Exception as exc:  # noqa: BLE001
        report.checks.append(ApiCheck("商品列表 pageList", False, _exc_text(exc), endpoint))
        return ""

    page = (data or {}).get("page")
    if not isinstance(page, dict) or not isinstance(page.get("list"), list):
        report.checks.append(
            ApiCheck("商品列表 pageList", False, "返回里没有 data.page.list（结构可能变了）", endpoint)
        )
        return ""

    rows = page.get("list") or []
    first_id = str(rows[0].get("id")) if rows and isinstance(rows[0], dict) else ""
    report.checks.append(
        ApiCheck(
            "商品列表 pageList",
            True,
            f"读到 {len(rows)} 条样本（totalSize={page.get('totalSize')}）",
            endpoint,
        )
    )
    return first_id


def _check_album(client: DxmClient, report: ApiReport) -> None:
    endpoint = _first_endpoint(client, "album_index")
    try:
        data = client.album_index()
    except Exception as exc:  # noqa: BLE001
        report.checks.append(ApiCheck("图片空间 album", False, _exc_text(exc), endpoint))
        return

    payload = (data or {}).get("data") or {}
    summary = payload.get("albumUserSummary")
    if not isinstance(summary, dict):
        report.checks.append(
            ApiCheck("图片空间 album", False, "返回里没有 albumUserSummary（结构可能变了）", endpoint)
        )
        return

    allow = int(summary.get("sizeAllow") or 0)
    used = int(summary.get("sizeUsed") or 0)
    if allow and used >= allow:
        detail = f"已满：{used / 1048576:.1f}/{allow / 1048576:.1f} MB（{summary.get('picNum')} 张）"
    elif allow:
        detail = f"可用：{used / 1048576:.1f}/{allow / 1048576:.1f} MB（{summary.get('picNum')} 张）"
    else:
        detail = f"读到 {summary.get('picNum')} 张"
    report.checks.append(ApiCheck("图片空间 album", True, detail, endpoint))


def _check_product_edit(client: DxmClient, report: ApiReport, product_id: str) -> None:
    endpoint = _first_endpoint(client, "product_edit")
    if not product_id:
        report.checks.append(
            ApiCheck("商品详情 edit", True, "没有可用的商品 id，跳过（不影响判断）", endpoint, skipped=True)
        )
        return
    try:
        data = client.product_edit(product_id)
    except Exception as exc:  # noqa: BLE001
        report.checks.append(ApiCheck("商品详情 edit", False, _exc_text(exc), endpoint))
        return

    product = ((data or {}).get("data") or {}).get("product")
    if not isinstance(product, dict) or not product:
        report.checks.append(
            ApiCheck("商品详情 edit", False, "返回里没有 data.product（结构可能变了）", endpoint)
        )
        return
    report.checks.append(
        ApiCheck(
            "商品详情 edit",
            True,
            f"读到商品对象 {len(product)} 个字段（样本 id={product_id}）",
            endpoint,
        )
    )


def _exc_text(exc: Exception) -> str:
    if isinstance(exc, SiteChanged):
        return f"疑似接口变更：{exc}"
    return str(exc)


def write_report(cfg: Config, report: ApiReport) -> Path:
    """把体检结果写成 JSON，给 AI/人排障。"""
    out_dir = cfg.data_dir / "reports" / "diagnose"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-api.json"
    path.write_text(
        json.dumps(report.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def pick_login(cfg: Config, wanted: str = "") -> str:
    """挑一个能自检的登录身份：指定的优先，否则第一个有会话的。"""
    if wanted:
        return wanted
    for info in cfg.logins():
        if info.has_session:
            return info.login
    infos = cfg.logins()
    return infos[0].login if infos else ""
