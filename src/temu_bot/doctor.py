"""`temu-bot doctor`：环境自检。

每条检查给出 PASS / WARN / FAIL 与可执行建议；AI key 缺失只算 WARN（不阻塞 P0/P1）。
"""

from __future__ import annotations

import importlib.metadata as md
import importlib.util
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from temu_bot import toolbox
from temu_bot.browser.detect import describe_browser
from temu_bot.config import Config, mask_secret
from temu_bot.dianxiaomi.session import session_path
from temu_bot.sheet.table import SheetTable

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"


@dataclass(slots=True)
class Check:
    name: str
    level: str
    detail: str


def _python_check() -> Check:
    ver = ".".join(str(v) for v in sys.version_info[:3])
    if sys.version_info[:2] != (3, 12):
        return Check("Python 版本", WARN, f"当前 {ver}，建议 3.12（uv 里有 3.12.13）")
    return Check("Python 版本", PASS, ver)


# (发行包名, import 用的模块名, 是不是"还没用上、缺了也不算错")
# pillow 的 import 名是 PIL，psd-tools 是 psd_tools —— 名字对不上会误报缺失。
# psd-tools 现在没有任何代码在用（PSD 套图还没做），打包时不会被收进去，
# 所以标成可选：真用上了再把它加进打包收集就行。
_DEPS: list[tuple[str, str, bool]] = [
    ("openpyxl", "openpyxl", False),
    ("playwright", "playwright", False),
    ("pillow", "PIL", False),
    ("pydantic", "pydantic", False),
    ("httpx", "httpx", False),
    ("typer", "typer", False),
    ("rich", "rich", False),
    ("psd-tools", "psd_tools", True),
]


def _deps_check() -> Check:
    """依赖在不在。

    打包成 exe 之后**没有 .dist-info 元数据**了（依赖都被塞进包里），
    拿 `md.version()` 查会一律报"缺少"——那是假警报。打包版改用
    "这个模块导不入得进来"来判断。
    """
    frozen = bool(getattr(sys, "frozen", False))
    missing: list[str] = []
    optional_missing: list[str] = []
    versions: list[str] = []

    for dist_name, module, optional in _DEPS:
        if frozen:
            try:
                found = importlib.util.find_spec(module) is not None
            except (ImportError, ValueError):
                found = False
            if found:
                versions.append(dist_name)
            elif optional:
                optional_missing.append(dist_name)
            else:
                missing.append(dist_name)
            continue
        try:
            versions.append(f"{dist_name} {md.version(dist_name)}")
        except md.PackageNotFoundError:
            (optional_missing if optional else missing).append(dist_name)

    if missing:
        hint = "（打包版缺了这些模块，请重新打包）" if frozen else "（跑 uv sync）"
        return Check("依赖包", FAIL, "缺少：" + ", ".join(missing) + hint)

    tail = f"；{', '.join(optional_missing)} 未装（还没用上，不影响）" if optional_missing else ""
    if frozen:
        return Check("依赖包", PASS, f"打包版，已内置 {len(versions)} 个依赖{tail}")
    return Check(
        "依赖包",
        PASS,
        ", ".join(versions[:4]) + (" …" if len(versions) > 4 else "") + tail,
    )


def _table_check(cfg: Config) -> Check:
    table = cfg.project.table
    if not table.exists():
        return Check("商品表", FAIL, f"找不到 {table}")
    try:
        st = SheetTable.from_config(cfg)
        problems = st.validate_headers()
        stats = st.stats()
    except Exception as exc:  # noqa: BLE001 - doctor 要把任何异常变成一条可读结论
        return Check("商品表", FAIL, f"读取失败：{exc}")

    if problems:
        return Check("商品表", FAIL, "表头与契约不一致：\n      - " + "\n      - ".join(problems))

    by_status = "、".join(f"{k} {v}" for k, v in stats["by_status"].items()) or "无"
    by_shop = "、".join(f"{k} {v}" for k, v in stats["by_shop"].items()) or "无"
    return Check(
        "商品表",
        PASS,
        f"{table.name}｜{stats['rows']} 行｜状态：{by_status}｜店铺：{by_shop}",
    )


def _toolbox_check(cfg: Config) -> Check:
    """工具箱自检。

    四个工具（SKU 重命名 / 导出清单 / 格式转换 / Excel 合并）**已经原生并进本程序**，
    所以这里只看两件事：**ID 台账**在不在（号码绝不能重发）、**Pillow** 支不支持
    avif/webp（格式转换要用）。桌面那个《图片批处理工具箱》只影响旧台账的合并，
    **不再是必需项**——它被删掉了也不影响这四个工具。
    """
    info = toolbox.selfcheck(cfg)
    pillow = info["pillow"]
    if not (pillow["avif"] and pillow["webp"]):
        return Check(
            "工具箱",
            FAIL,
            f"Pillow {pillow['version']} 不支持 avif/webp，格式转换会不可用",
        )

    ledger = info["ledger"]
    broken = [str(f["path"]) for f in ledger["files"] if f.get("error")]
    if broken:
        return Check("工具箱", FAIL, f"台账读不了：{'；'.join(broken)}")

    detail = (
        f"已用 ID {ledger['merged_total']} 个｜台账 {ledger['ledger']}"
        f"（{'已建' if ledger['ledger_exists'] else '首次使用时新建'}）"
        f"｜Pillow {pillow['version']} avif/webp 可用"
    )
    if not pillow["heic"]:
        detail += "；heic 转不了"
    return Check("工具箱", PASS, detail)


def _browser_check(cfg: Config) -> Check:
    """报告"实际会用哪个浏览器"——按配置优先级，而不是只看自带 Chromium。"""
    try:
        version = md.version("playwright")
    except md.PackageNotFoundError:
        # 打包版没有 .dist-info；能 import 进来就说明它内置了
        if getattr(sys, "frozen", False) and importlib.util.find_spec("playwright"):
            version = "（打包版内置）"
        else:
            return Check("浏览器", FAIL, "playwright 未安装")

    info = describe_browser(cfg)
    kind = str(info["kind"])
    path = str(info["path"] or "")
    note = str(info["note"] or "")
    ver = f" {info['version']}" if info["version"] else ""

    if path and Path(path).exists():
        level = PASS if "没找到" not in kind and "不存在" not in kind else FAIL
        return Check("浏览器", level, f"playwright {version}｜{kind}{ver}｜{path}")

    if note:
        return Check("浏览器", FAIL, f"playwright {version}｜{kind}｜{note}")
    return Check("浏览器", FAIL, f"playwright {version}｜{kind}｜没找到可用浏览器")


def _ai_check(cfg: Config) -> Check:
    if not cfg.ai.chain:
        return Check("AI 标题", WARN, "未配置 ai.chain —— 本期只用表内标题（P3 再开）")
    lines: list[str] = []
    missing = 0
    for pid in cfg.ai.chain:
        provider = cfg.provider(pid)
        if provider is None:
            lines.append(f"{pid}(未定义)")
            missing += 1
            continue
        key = cfg.ai_key(provider)
        if key:
            lines.append(f"{pid}:{provider.model}(key {mask_secret(key)})")
        else:
            lines.append(f"{pid}:{provider.model}(缺 {provider.api_key_env})")
            missing += 1
    level = PASS if missing == 0 else WARN
    return Check("AI 标题", level, "｜".join(lines))


def _dirs_check(cfg: Config) -> Check:
    bad: list[str] = []
    for path in (cfg.data_dir / "logs", cfg.data_dir / "backups", cfg.work_dir, cfg.root / "profiles"):
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe_file = path / ".write_probe"
            probe_file.write_text("ok", encoding="utf-8")
            probe_file.unlink()
        except OSError as exc:
            bad.append(f"{path}（{exc}）")
    if bad:
        return Check("可写目录", FAIL, "不可写：" + "；".join(bad))
    return Check("可写目录", PASS, f"{cfg.root}（logs/work/profiles/backups）")


def _shops_check(cfg: Config) -> Check:
    """报告**有哪些登录身份**、各自的登录态齐不齐。

    只看配置里那份静态店铺表是不够的——**店铺不固定、登录的店铺随时会改**，
    所以真正的来源是磁盘上的痕迹（`profiles/` + `data/state/session_*.json`）。
    配置里的店铺名只当一句提示。
    """
    logins = cfg.logins()
    if not logins:
        return Check(
            "登录身份",
            WARN,
            "一个都没找到——先跑 1-登录店小秘.bat 登录一次",
        )

    parts: list[str] = []
    warn = False
    for info in logins:
        label = info.login + (f"（配置里记的是「{info.shop_hint}」）" if info.shop_hint else "")
        if info.has_session and info.has_profile:
            parts.append(f"{label}(已登录)")
        elif info.has_session or info.has_profile:
            parts.append(
                f"{label}(半个登录态：接口{'有' if info.has_session else '无'}/"
                f"浏览器{'有' if info.has_profile else '无'})"
            )
            warn = True
        else:
            parts.append(f"{label}(待登录)")
            warn = True
    return Check("登录身份", WARN if warn else PASS, "｜".join(parts))


def _busy_check(cfg: Config) -> Check:
    locks = [p.name for p in cfg.project.table.parent.glob("~$*")] if cfg.project.table.parent.exists() else []
    if locks:
        return Check("表格占用", WARN, f"检测到 Office 锁文件 {locks[0]}（表正被打开，回写会失败）")
    return Check("表格占用", PASS, "无锁文件，可独占写")


def run_doctor(cfg: Config) -> list[Check]:
    return [
        _python_check(),
        _deps_check(),
        _table_check(cfg),
        _busy_check(cfg),
        _toolbox_check(cfg),
        _browser_check(cfg),
        _ai_check(cfg),
        _dirs_check(cfg),
        _shops_check(cfg),
    ]


def has_failure(checks: list[Check]) -> bool:
    return any(c.level == FAIL for c in checks)
