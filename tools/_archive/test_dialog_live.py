"""真的弹一次对话框，并枚举窗口确认它**确实出现在屏幕上**（以及是不是前台）。

做法：后台线程发请求 → 主线程枚举所有顶层窗口，找标题匹配的那个。
跑完自动关掉对话框。
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import urllib.request

ENUM_PS = r"""
Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public class Win {
  [DllImport("user32.dll")] static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
  [DllImport("user32.dll")] static extern int GetWindowText(IntPtr hWnd, StringBuilder s, int nMaxCount);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr hWnd);
  [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
  delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
  public static List<string> List() {
    var outp = new List<string>();
    IntPtr fg = GetForegroundWindow();
    EnumWindows((h, l) => {
      if (!IsWindowVisible(h)) return true;
      var sb = new StringBuilder(512);
      GetWindowText(h, sb, sb.Capacity);
      string t = sb.ToString();
      if (t.Length > 0) {
        uint pid; GetWindowThreadProcessId(h, out pid);
        outp.Add((h == fg ? "FOREGROUND|" : "|") + pid + "|" + t);
      }
      return true;
    }, IntPtr.Zero);
    return outp;
  }
}
'@
[Win]::List() | ForEach-Object { $_ }
"""


def list_windows() -> list[str]:
    out = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", ENUM_PS],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    return [ln.strip() for ln in (out.stdout or "").splitlines() if ln.strip()]


url = "http://127.0.0.1:8765/api/dialog/folder"
body = json.dumps({"title": "选择素材文件夹", "initial": "", "filters": ""}).encode()

result: dict = {}


def call():
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            result["body"] = resp.read().decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)
    result["ms"] = round((time.perf_counter() - t0) * 1000)


print("发起请求…")
thread = threading.Thread(target=call, daemon=True)
thread.start()
time.sleep(6)          # 给对话框一点时间弹出来

wins = list_windows()
hits = [w for w in wins if "选择素材文件夹" in w]
print(f"\n枚举到 {len(wins)} 个可见顶层窗口")
if hits:
    for h in hits:
        fg = "★前台" if h.startswith("FOREGROUND") else "（在后台！）"
        print(f"  ✅ 找到对话框窗口：{h}　{fg}")
else:
    print("  ❌ 没找到标题含「选择素材文件夹」的可见窗口")
    print("     当前可见窗口里有：")
    for w in wins[:25]:
        print(f"       {w}")

# 关掉它
subprocess.run(
    ["powershell.exe", "-NoProfile", "-Command",
     "$ws=New-Object -ComObject WScript.Shell; $null=$ws.AppActivate('选择素材文件夹'); "
     "Start-Sleep -Milliseconds 400; $ws.SendKeys('{ESC}')"],
    capture_output=True, text=True, timeout=60,
)
thread.join(timeout=20)
print(f"\n请求结果：{result}")
