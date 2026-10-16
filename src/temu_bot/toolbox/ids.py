"""11 位唯一 ID + 台账。

**这些 ID 一旦发出去就永远不能重发**——文件名上的 ID 是图片的唯一身份，
重发会让两张图撞号、导出的清单直接作废。所以：

- 生成用**密码学随机数**（`secrets`），不是 `random`。
  桌面那个工具箱原本用 `%random%`（可预测），后来换成了 `RandomNumberGenerator`，
  这里照它现在的做法。
- 台账（`data\\state\\used_ids.txt`）**只增不删**，一行一个 ID，UTF-8 无 BOM，
  批量结束后一次性追加（不是每个 ID 写一次）。
- 判定"用过没有"时 = **项目台账 ∪ 旧台账**。旧台账指桌面那个工具箱的
  `used_ids.txt`——它已经发过 130 个号，搬家时不能丢。取并集是幂等的，
  所以不用做一次性迁移，也不用记"迁过了没有"。

实现在这儿是为了能单测；桌面工具的 `New-UniqueSkuId` / `Get-LedgerSet` /
`Save-LedgerIds` 是本文件的对照物。
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path

from temu_bot.config import Config

log = logging.getLogger(__name__)

DEFAULT_ID_LENGTH = 11
MAX_ATTEMPTS = 2000


class LedgerError(RuntimeError):
    pass


def read_ledger(path: Path) -> set[str]:
    """读一个台账文件成集合。文件不在就当空；空行忽略。"""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return set()
    except OSError as exc:  # 权限之类：宁可报错也别当成"没人用过"
        raise LedgerError(f"读不了台账 {path}：{exc}") from exc
    return {line.strip() for line in text.splitlines() if line.strip()}


def ledger_paths(cfg: Config) -> list[Path]:
    """所有要看的台账：项目自己的那个 + 要合并的旧台账（去重，按顺序）。"""
    seen: list[Path] = []
    for path in [cfg.toolbox_ledger, *cfg.toolbox_seed_ledgers]:
        if path and path not in seen:
            seen.append(path)
    return seen


def load_taken(cfg: Config) -> set[str]:
    """所有已用过的 ID（项目台账 ∪ 旧台账）。"""
    taken: set[str] = set()
    for path in ledger_paths(cfg):
        taken |= read_ledger(path)
    return taken


def new_unique_id(taken: set[str], length: int = DEFAULT_ID_LENGTH) -> str:
    """生成一个没在 `taken` 里的 ID，并**就地加进 `taken`**。

    每一位数字来自一个独立随机字节（`48 + byte % 10`），与桌面工具一致。
    """
    if length < 1:
        raise LedgerError(f"ID 位数不合理：{length}")
    for _ in range(MAX_ATTEMPTS):
        candidate = "".join(chr(48 + b % 10) for b in secrets.token_bytes(length))
        if candidate not in taken:
            taken.add(candidate)
            return candidate
    raise LedgerError(
        f"连续 {MAX_ATTEMPTS} 次都生成了重复 ID，ID 空间可能已耗尽"
        f"（当前已用 {len(taken)} 个）"
    )


def save_ids(cfg: Config, ids: list[str]) -> Path | None:
    """把新发的 ID 追加进项目台账。没有新 ID 就什么都不做。"""
    wanted = [i for i in ids if i]
    if not wanted:
        return None
    path = cfg.toolbox_ledger
    path.parent.mkdir(parents=True, exist_ok=True)
    # 无 BOM 的 UTF-8；追加写（台账只增不删）
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        for item in wanted:
            fh.write(item + "\n")
    log.info("台账追加 %d 个 ID → %s", len(wanted), path)
    return path


def describe(cfg: Config) -> dict[str, object]:
    """自检用的一小段摘要：台账在哪、各有多少条。"""
    project = cfg.toolbox_ledger
    rows = []
    for path in ledger_paths(cfg):
        try:
            count = len(read_ledger(path))
        except LedgerError as exc:
            rows.append({"path": str(path), "count": -1, "error": str(exc)})
            continue
        rows.append(
            {
                "path": str(path),
                "count": count,
                "exists": path.is_file(),
                "is_project": path == project,
            }
        )
    return {
        "ledger": str(project),
        "ledger_exists": project.is_file(),
        "merged_total": len(load_taken(cfg)),
        "files": rows,
    }
