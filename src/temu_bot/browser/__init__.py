"""浏览器适配层与页面探测。"""

from temu_bot.browser import base, inspect, playwright_local
from temu_bot.browser.playwright_local import PlaywrightLocalProvider, PlaywrightLocalSession

__all__ = [
    "base",
    "inspect",
    "playwright_local",
    "PlaywrightLocalProvider",
    "PlaywrightLocalSession",
]
