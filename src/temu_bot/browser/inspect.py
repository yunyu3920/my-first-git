"""页面探测：登录态判定 + 结构化快照（可交互元素清单 / 标题 / 截图 / HTML）。

为什么要"元素清单"：写选择器时，一个 500KB 的 HTML 远不如"页面上有哪些按钮、
输入框，各自的可访问名/占位符/可见文本是什么"有用。probe 命令把页面整理成
data/reports/probe/<label>.json + .png + .html，据此写 config/selectors/temu.yaml。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from temu_bot.config import Config

log = logging.getLogger(__name__)

SELECTORS_RELATIVE = Path("config") / "selectors" / "temu.yaml"

# 内置兜底：即使选择器配置文件丢了/还没补全，也不能把"登录页"误判成"已登录"。
DEFAULT_LOGIN_URL_PATTERNS: tuple[str, ...] = (
    "/login",
    "/passport",
    "/signin",
    "/sign-in",
    "sso",
    "account/login",
)

_INVENTORY_JS = r"""
() => {
  const pick = (el, attrs) => {
    for (const a of attrs) {
      const v = el.getAttribute(a);
      if (v && String(v).trim()) return String(v).trim();
    }
    return '';
  };
  const selector = [
    'a[href]', 'button', 'input', 'select', 'textarea',
    '[role="button"]', '[role="link"]', '[role="tab"]', '[role="checkbox"]',
    '[contenteditable="true"]', '[data-testid]', '[data-tid]'
  ].join(',');
  const out = [];
  const seen = new Set();
  for (const el of document.querySelectorAll(selector)) {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    const visible = rect.width > 0 && rect.height > 0 &&
                    style.visibility !== 'hidden' && style.display !== 'none';
    if (!visible) continue;
    let text = (el.innerText || el.value || el.getAttribute('title') || '').trim();
    text = text.replace(/\s+/g, ' ').slice(0, 80);
    const item = {
      tag: el.tagName.toLowerCase(),
      type: pick(el, ['type']),
      role: pick(el, ['role']),
      name: pick(el, ['name']),
      id: pick(el, ['id']),
      testid: pick(el, ['data-testid', 'data-tid']),
      aria: pick(el, ['aria-label']),
      placeholder: pick(el, ['placeholder']),
      cls: (typeof el.className === 'string' ? el.className : '').slice(0, 100),
      text: text,
      href: pick(el, ['href']).slice(0, 200),
      x: Math.round(rect.x), y: Math.round(rect.y)
    };
    const key = [item.tag, item.type, item.name, item.id, item.testid, item.aria, item.placeholder, item.text, item.href].join('|');
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(item);
    if (out.length >= 400) break;
  }
  const headings = Array.from(document.querySelectorAll('h1,h2,h3,h4,legend,label'))
    .map(el => (el.innerText || '').replace(/\s+/g, ' ').trim())
    .filter(t => t).slice(0, 80);
  return {
    title: document.title,
    url: location.href,
    headings: headings,
    counts: {a: document.querySelectorAll('a').length, button: document.querySelectorAll('button').length,
             input: document.querySelectorAll('input').length, iframe: document.querySelectorAll('iframe').length,
             table_rows: document.querySelectorAll('tr').length},
    elements: out
  };
}
"""


# ------------------------------------------------------------------ 选择器配置
def selectors_path(cfg: Config) -> Path:
    return cfg.root / SELECTORS_RELATIVE


def load_selectors(cfg: Config) -> dict[str, Any]:
    path = selectors_path(cfg)
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("选择器配置读不了（%s）：%s", path, exc)
        return {}


# ------------------------------------------------------------------ 登录态
def looks_logged_in(cfg: Config, url: str, page: Any) -> bool:
    """判定是否处于登录后状态。

    判据优先级：URL 命中登录页特征 → 未登录；配置里的"已登录"候选元素可见 → 已登录；
    没配候选元素时，只要不是登录页且不在 about:blank，就宽松认为已登录
    （P1 探测拿到真实元素后再把 candidates 补上，判定就会精确）。
    """
    sel = load_selectors(cfg).get("login", {}) or {}
    url_l = (url or "").lower()

    patterns = sel.get("url_patterns") or DEFAULT_LOGIN_URL_PATTERNS
    for pattern in patterns:
        if pattern and str(pattern).lower() in url_l:
            return False

    candidates = [c for c in (sel.get("logged_in_candidates") or []) if c]
    if candidates:
        for candidate in candidates:
            try:
                if page.locator(candidate).first.is_visible(timeout=800):
                    return True
            except Exception:  # noqa: BLE001 - 元素不在/超时都算"这条候选不成立"
                continue
        return False

    if not url or url_l.startswith("about:blank") or url_l.startswith("chrome"):
        return False
    return True


# ------------------------------------------------------------------ 快照
def collect(page: Any) -> dict[str, Any]:
    """抽取页面结构（失败时返回最小可用结构，绝不因此中断探测）。"""
    try:
        data = page.evaluate(_INVENTORY_JS)
    except Exception as exc:  # noqa: BLE001
        log.warning("页面结构抽取失败：%s", exc)
        data = {"title": "", "url": "", "headings": [], "counts": {}, "elements": []}
        try:
            data["title"] = page.title()
            data["url"] = page.url
        except Exception:  # noqa: BLE001
            pass
    return data


def dump_page(
    cfg: Config,
    page: Any,
    label: str,
    *,
    out_dir: Path | None = None,
    shop: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Path]:
    """把当前页面存成 <label>.json / .png / .html，返回各文件路径。"""
    out_dir = out_dir or (cfg.data_dir / "reports" / "probe")
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in label) or "page"

    data = collect(page)
    data.update(
        {
            "label": label,
            "shop": shop,
            "captured_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    if extra:
        data["extra"] = extra

    json_path = out_dir / f"{safe}.json"
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    paths: dict[str, Path] = {"json": json_path}

    png_path = out_dir / f"{safe}.png"
    try:
        page.screenshot(path=str(png_path), full_page=True)
        paths["png"] = png_path
    except Exception as exc:  # noqa: BLE001 - 超长页面 full_page 可能失败，退成视口截图
        log.warning("整页截图失败（%s），改用视口截图", exc)
        try:
            page.screenshot(path=str(png_path))
            paths["png"] = png_path
        except Exception as exc2:  # noqa: BLE001
            log.warning("截图也失败：%s", exc2)

    html_path = out_dir / f"{safe}.html"
    try:
        html_path.write_text(page.content(), encoding="utf-8")
        paths["html"] = html_path
    except Exception as exc:  # noqa: BLE001
        log.warning("保存 HTML 失败：%s", exc)

    return paths


def summarize(data: dict[str, Any], limit: int = 40) -> list[str]:
    """把快照整理成终端里能一眼看的几行（供 CLI 直接打印）。"""
    lines = [
        f"标题：{data.get('title', '')}",
        f"地址：{data.get('url', '')}",
        "计数：" + json.dumps(data.get("counts", {}), ensure_ascii=False),
    ]
    headings = data.get("headings") or []
    if headings:
        lines.append("标题/标签：" + "、".join(headings[:12]))

    elements = data.get("elements") or []
    lines.append(f"可交互元素：{len(elements)} 个（列前 {min(limit, len(elements))} 个）")
    for el in elements[:limit]:
        bits = [el.get("tag", "")]
        if el.get("role"):
            bits.append(f"role={el['role']}")
        if el.get("type"):
            bits.append(f"type={el['type']}")
        if el.get("testid"):
            bits.append(f"data-testid={el['testid']}")
        if el.get("name"):
            bits.append(f"name={el['name']}")
        if el.get("aria"):
            bits.append(f"aria={el['aria']}")
        if el.get("placeholder"):
            bits.append(f"ph={el['placeholder']}")
        if el.get("text"):
            bits.append(f"text=「{el['text']}」")
        lines.append("  - " + " ".join(bits))
    return lines
