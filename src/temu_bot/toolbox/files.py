"""工具箱扫文件夹的两条规矩。

**不共用 `imaging/resolve.py` 的 `IMAGE_EXTS`**：那边是上架主流程用的，
多一类文件就可能影响找图；这里照桌面《图片批处理工具箱》那套来，
比它多一个 `.heic`（清单在 `Core.ps1` 的 `$script:ImageExtensions`）。
"""

from __future__ import annotations

from pathlib import Path

# 桌面工具的 ImageExtensions，一模一样
IMAGE_EXTS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".bmp",
    ".tif",
    ".tiff",
    ".avif",
    ".heic",
)

# Excel 合并看得上的（老 .xls 也列进来，但读不了——见 merge.py）
WORKBOOK_EXTS = (".xls", ".xlsx")


def list_images(folder: Path, *, recursive: bool = False) -> list[Path]:
    """文件夹里的图片，**按名字排序**（桌面工具就是 Sort-Object Name）。

    默认只看这一层——要递归得显式说，跟桌面工具的 `-Recurse` 一致。
    """
    if not folder.is_dir():
        raise NotADirectoryError(f"文件夹不存在：{folder}")
    walker = folder.rglob("*") if recursive else folder.glob("*")
    got = [
        p
        for p in walker
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    ]
    return sorted(got, key=lambda p: p.name)


def list_workbooks(folder: Path, *, exclude: Path | None = None) -> list[Path]:
    """文件夹里的 .xls/.xlsx，排除 `~$` 锁文件和**输出文件自身**。

    排除自己那份是有原因的：输出默认就落在这个目录里，不排除的话第二次跑
    会把上一次的结果也当输入再合并一遍。
    """
    if not folder.is_dir():
        raise NotADirectoryError(f"文件夹不存在：{folder}")
    skip = exclude.resolve() if exclude else None
    got: list[Path] = []
    for p in folder.glob("*"):
        if not p.is_file() or p.suffix.lower() not in WORKBOOK_EXTS:
            continue
        if p.name.startswith("~$"):
            continue
        if skip is not None and p.resolve() == skip:
            continue
        got.append(p)
    return sorted(got, key=lambda p: p.name)
