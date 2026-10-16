"""验证系统文件对话框能不能真的弹出来。

判据（对话框是模态的，没法自动点）：
  - 超时未退出  → 脚本编译通过、窗口弹出来了  ✅
  - 立刻报错返回 → 编译/调用有问题           ❌
会在 8 秒后强制杀掉子进程（对话框随之关闭）。
"""

from __future__ import annotations

import subprocess
import sys
import time

sys.path.insert(0, "src")

from temu_bot.web.dialogs import _PS_TEMPLATE, _b64, _ps_quote  # noqa: E402

script = (
    _PS_TEMPLATE.replace("__MODE__", _ps_quote("folder"))
    .replace("__TITLE__", _ps_quote("【测试】选一个文件夹（8 秒后自动关闭）"))
    .replace("__INITIAL__", _ps_quote(""))
    .replace("__FILTER__", _ps_quote(""))
)

t0 = time.perf_counter()
proc = subprocess.Popen(
    ["powershell.exe", "-NoProfile", "-STA", "-EncodedCommand", _b64(script)],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    encoding="utf-8",
    errors="replace",
)
try:
    out, err = proc.communicate(timeout=8)
    dt = time.perf_counter() - t0
    print(f"❌ 进程在 {dt:.1f}s 内就退出了 —— 说明没弹出对话框")
    print(f"   退出码: {proc.returncode}")
    print(f"   stdout: {(out or '').strip()[:600]}")
    print(f"   stderr: {(err or '').strip()[:1200]}")
    sys.exit(1)
except subprocess.TimeoutExpired:
    proc.kill()
    proc.communicate()
    print("✅ 脚本编译通过，且对话框保持打开（超时被杀掉）—— 说明窗口真的弹出来了")
    print("   （刚才屏幕上应该闪过一个「选择文件夹」窗口，已自动关闭）")
