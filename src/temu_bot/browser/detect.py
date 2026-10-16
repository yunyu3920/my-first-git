"""识别"实际会用哪个浏览器二进制"，供 doctor / selfcheck 显示。

配置优先级与 playwright_local 保持一致：
    executable_path  >  channel  >  Playwright 自带 Chromium
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from temu_bot.config import Config

CHROME_CANDIDATES = (
    r"{pf}\Google\Chrome\Application\chrome.exe",
    r"{pf86}\Google\Chrome\Application\chrome.exe",
    r"{localappdata}\Google\Chrome\Application\chrome.exe",
)
EDGE_CANDIDATES = (
    r"{pf86}\Microsoft\Edge\Application\msedge.exe",
    r"{pf}\Microsoft\Edge\Application\msedge.exe",
)
VERSION_DIR = re.compile(r"^\d+\.\d+\.\d+\.\d+$")


def _expand(template: str) -> Path | None:
    mapping = {
        "{pf}": os.environ.get("ProgramFiles", r"C:\Program Files"),
        "{pf86}": os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        "{localappdata}": os.environ.get("LOCALAPPDATA", ""),
    }
    for key, value in mapping.items():
        template = template.replace(key, value)
    path = Path(template)
    return path if path.exists() else None


def _version_of(exe: Path) -> str:
    """版本号：优先读同目录下的版本号文件夹名（Chrome 就是这么排版的），读不到就试 --version。"""
    parent = exe.parent
    candidates = [
        child.name
        for child in parent.iterdir()
        if child.is_dir() and VERSION_DIR.match(child.name)
    ] if parent.exists() else []
    if candidates:
        candidates.sort(key=lambda s: [int(x) for x in s.split(".")])
        return candidates[-1]

    try:
        import subprocess

        out = subprocess.run(
            [str(exe), "--version"], capture_output=True, text=True, timeout=8, errors="replace"
        )
        text = (out.stdout or out.stderr or "").strip()
        match = re.search(r"(\d+\.\d+\.\d+\.\d+)", text)
        if match:
            return match.group(1)
    except Exception:  # noqa: BLE001 - 拿不到版本不该影响主流程
        pass
    return ""


def detect_by_channel(channel: str) -> Path | None:
    channel = (channel or "").lower()
    if channel in ("chrome", "google-chrome"):
        candidates = CHROME_CANDIDATES
    elif channel in ("msedge", "edge"):
        candidates = EDGE_CANDIDATES
    else:
        return None
    for template in candidates:
        found = _expand(template)
        if found:
            return found
    return None


def bundled_chromium() -> Path | None:
    """Playwright 自带 Chromium 的可执行文件（不启动浏览器，只问路径）。"""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            path = Path(p.chromium.executable_path)
            return path if path.exists() else None
    except Exception:  # noqa: BLE001
        return None


def describe_browser(cfg: Config) -> dict[str, object]:
    """返回 {kind, path, version, note}——kind 说明用的是哪个来源。"""
    browser = cfg.browser

    if browser.executable_path:
        path = Path(browser.executable_path)
        if path.exists():
            return {
                "kind": "指定路径",
                "path": str(path),
                "version": _version_of(path),
                "note": "config.browser.executable_path",
            }
        return {
            "kind": "指定路径（不存在）",
            "path": str(path),
            "version": "",
            "note": "配置里的路径无效，检查 browser.executable_path",
        }

    if browser.channel:
        found = detect_by_channel(browser.channel)
        if found:
            return {
                "kind": f"系统 {browser.channel}",
                "path": str(found),
                "version": _version_of(found),
                "note": "config.browser.channel",
            }
        return {
            "kind": f"系统 {browser.channel}（没找到）",
            "path": "",
            "version": "",
            "note": "没找到该 channel 的浏览器，可改用 browser.executable_path 指定全路径",
        }

    path = bundled_chromium()
    return {
        "kind": "Playwright 自带 Chromium",
        "path": str(path) if path else "",
        "version": "",
        "note": "" if path else "自带浏览器缺失，可跑 playwright install chromium",
    }


def describe_one_line(cfg: Config) -> str:
    info = describe_browser(cfg)
    version = f" {info['version']}" if info["version"] else ""
    path = info["path"] or "（未找到）"
    return f"{info['kind']}{version}｜{path}"
