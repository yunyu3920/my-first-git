"""旧路子：以子进程方式调用桌面《图片批处理工具箱》的 Core.ps1。

四个主力功能（SKU 重命名 / 导出清单 / 格式转换 / Excel 合并）**已经原生并入**
（见同目录的 `rename.py` / `listing.py` / `convert.py` / `merge.py`），不再需要这个桥。
这里留着是因为 `doctor` 的「工具箱」自检还在用它——桌面那个工具本身还能用，
自检能顺带告诉你它还健不健在。

要点（照旧）：
- 不逐文件调用（PowerShell 冷启动 0.3–0.8s），一律按文件夹批量传参。
- 中文路径不能走命令行参数，所以脚本写成 UTF-8 **带 BOM** 的临时 .ps1 再 -File 执行
  （PowerShell 5.1 只有带 BOM 才会按 UTF-8 解析）。
- 输出统一按 UTF-8 读，脚本开头先设 [Console]::OutputEncoding。
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path

from temu_bot.config import Config

log = logging.getLogger(__name__)

PRELUDE = """$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
"""


class ToolboxError(RuntimeError):
    pass


def _ps_quote(text: str) -> str:
    """PowerShell 单引号字符串：内部的单引号要写两遍。"""
    return "'" + str(text).replace("'", "''") + "'"


def run_ps(cfg: Config, body: str, *, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    """dot-source Core.ps1 后执行 body，返回 CompletedProcess（text 模式）。"""
    core = cfg.toolbox.core_path
    if not core.exists():
        raise ToolboxError(f"找不到工具箱核心脚本：{core}")

    script = PRELUDE + f". {_ps_quote(str(core))}\n" + body + "\n"
    with tempfile.TemporaryDirectory(prefix="temu_bot_ps_") as tmp:
        job = Path(tmp) / "job.ps1"
        job.write_text(script, encoding="utf-8-sig")
        proc = subprocess.run(
            [
                cfg.toolbox.powershell,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(job),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-2000:]
        log.error("PowerShell 调用失败 rc=%s：%s", proc.returncode, tail)
    return proc


def probe(cfg: Config) -> dict[str, object]:
    """doctor 用：确认 Core.ps1 可 dot-source、关键函数可解析、台账文件存在。"""
    body = (
        "$names = @('Invoke-SkuRename','Invoke-ExportImageList','Invoke-ImageConvert',"
        "'New-UniqueSkuId','Get-LedgerSet')\n"
        "$found = @()\n"
        "foreach ($n in $names) { if (Get-Command $n -ErrorAction SilentlyContinue) { $found += $n } }\n"
        "Write-Output ('FUNCS=' + ($found -join ','))\n"
        f"Write-Output ('LEDGER=' + (Test-Path {_ps_quote(str(cfg.toolbox.ledger))}))\n"
        f"Write-Output ('NODE=' + [bool](Get-Command {cfg.toolbox.node} -ErrorAction SilentlyContinue))\n"
    )

    result: dict[str, object] = {
        "core": cfg.toolbox.core_path,
        "core_exists": cfg.toolbox.core_path.exists(),
        "ledger": cfg.toolbox.ledger,
        "ledger_exists": cfg.toolbox.ledger.exists(),
        "functions": [],
        "node": False,
        "ok": False,
        "detail": "",
    }

    if not result["core_exists"]:
        result["detail"] = f"缺失 {cfg.toolbox.core_path}"
        return result

    try:
        proc = run_ps(cfg, body, timeout=120)
    except (ToolboxError, subprocess.TimeoutExpired) as exc:
        result["detail"] = f"调用失败：{exc}"
        return result

    funcs: list[str] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("FUNCS="):
            funcs = [f for f in line[6:].split(",") if f]
        elif line.startswith("LEDGER="):
            result["ledger_exists"] = line[7:].strip().lower() == "true"
        elif line.startswith("NODE="):
            result["node"] = line[5:].strip().lower() == "true"

    result["functions"] = funcs
    result["ok"] = proc.returncode == 0 and bool(funcs)
    if not result["ok"]:
        result["detail"] = (proc.stderr or proc.stdout or "").strip()[-500:] or "无输出"
    return result
