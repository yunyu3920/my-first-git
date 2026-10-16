"""上架前的输入校验：**必须同时给出「信息表格」和「图片文件夹」**。

这是用户明确要求的前置条件——
「上架时应要求给予图片文件夹与信息表格，根据信息表格中填写的图片信息
  去图片文件夹寻找后上传上架」。

所以开工前先把这两样验清楚：
  1. 表格：存在、能读、表头对得上契约、有多少行
  2. 图片文件夹：存在、是个目录、里面有多少张图
  3. 逐行：表里填的每个图片名，能不能在图片文件夹里**找**到
  4. 逐行：第 9 列「一键生成SKU货号前缀编码」填了没有
     （上架要用它当 SKU 货号；没有就只能沿用模板的，那会让一批商品共用一个货号）

任何一条不满足都要在**碰接口之前**报出来，别等跑一半才发现图不在。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from temu_bot.config import Config
from temu_bot.imaging.resolve import FolderIndex, Resolved, index_folder
from temu_bot.sheet.schema import SheetRow
from temu_bot.sheet.table import SheetTable

log = logging.getLogger(__name__)

# 表里这些列的图要真的找得到
IMAGE_SLOTS: tuple[str, ...] = ("main", "material")


class InputError(RuntimeError):
    """输入不齐/不可用——这个错要直接摆给用户看。"""


@dataclass(slots=True)
class RowReadiness:
    row_index: int
    row_key: str
    shop: str
    folder: Path
    resolved: list[Resolved] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


@dataclass(slots=True)
class InputsReport:
    table_path: Path
    table_ok: bool = False
    table_detail: str = ""
    images_dir: Path | None = None
    folder_ok: bool = False
    folder_detail: str = ""
    image_count: int = 0
    rows: list[RowReadiness] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.table_ok and self.folder_ok and all(r.ok for r in self.rows)

    @property
    def ready(self) -> list[RowReadiness]:
        return [r for r in self.rows if r.ok]

    @property
    def blocked(self) -> list[RowReadiness]:
        return [r for r in self.rows if not r.ok]

    def summary(self) -> str:
        if not self.table_ok:
            return f"表格不可用：{self.table_detail}"
        if not self.folder_ok:
            return f"图片文件夹不可用：{self.folder_detail}"
        return (
            f"表格 {len(self.rows)} 行；图片文件夹 {self.image_count} 张图；"
            f"可上架 {len(self.ready)} 行，被挡 {len(self.blocked)} 行"
        )


def check_folder(folder: Path | None) -> tuple[bool, str, FolderIndex | None, int]:
    """图片文件夹能不能用。"""
    if folder is None:
        return False, "没给图片文件夹", None, 0
    folder = Path(folder)
    if not folder.exists():
        return False, f"图片文件夹不存在：{folder}", None, 0
    if not folder.is_dir():
        return False, f"这不是一个文件夹：{folder}", None, 0

    index = index_folder(folder)
    if not index.files:
        return False, f"文件夹里一张图都没有：{folder}", index, 0
    return True, f"{folder}｜{len(index.files)} 张图", index, len(index.files)


def effective_folder(row: SheetRow, images_dir: Path | None) -> Path | None:
    """这一行的图去哪儿找：命令行给的文件夹优先，否则用表里第 1 列。"""
    if images_dir is not None:
        return Path(images_dir)
    return Path(row.folder) if row.folder else None


def check_rows(
    rows: list[SheetRow],
    *,
    images_dir: Path | None,
    index_cache: dict[Path, FolderIndex] | None = None,
) -> list[RowReadiness]:
    """逐行核对：表里写的图片名，在图片文件夹里找不找得到。"""
    cache = index_cache if index_cache is not None else {}
    out: list[RowReadiness] = []

    for row in rows:
        folder = effective_folder(row, images_dir)
        readiness = RowReadiness(
            row_index=row.row_index,
            row_key=row.row_key,
            shop=row.shop,
            folder=folder or Path(""),
        )

        if not row.sku_prefix_code:
            readiness.problems.append(
                "第 9 列「一键生成SKU货号前缀编码」是空的"
                "（上架要拿它当 SKU 货号，不能沿用模板的）"
            )

        if folder is None:
            readiness.problems.append("没有图片文件夹（表里第 1 列是空的，也没给 --images-dir）")
            out.append(readiness)
            continue

        index = cache.get(folder)
        if index is None:
            index = index_folder(folder)
            cache[folder] = index
        readiness.folder = folder

        if not index.exists:
            readiness.problems.append(f"图片文件夹不存在：{folder}")
            out.append(readiness)
            continue

        names = [n for slot in IMAGE_SLOTS for n in row.images.get(slot, [])]
        if not names:
            readiness.problems.append("表里没有任何图片列（主图/素材图）填了文件名")
            out.append(readiness)
            continue
        # 一行 = 主图 + 素材图这一对。缺素材图时素材图位会留着模板那张，
        # 上出来是个"半对"的商品（收集图片时本来就把这种行跳过，这里保持一致）。
        if not row.material_image:
            readiness.problems.append("没有「素材图」（缺素材图的行请先跳过，别硬上）")

        for name in names:
            got = index.resolve(name)
            readiness.resolved.append(got)
            if got.how == "ambiguous":
                readiness.problems.append(got.describe())
            elif not got.ok:
                readiness.problems.append(got.describe())

        out.append(readiness)

    return out


def check_inputs(
    cfg: Config,
    *,
    images_dir: Path | None = None,
    limit: int | None = None,
    row_key: str | None = None,
    row_index: int | None = None,
    skip_done: bool = False,
) -> InputsReport:
    """把「表格 + 图片文件夹」两样输入一次验完。"""
    report = InputsReport(table_path=Path(cfg.project.table))

    if not Path(cfg.project.table).exists():
        report.table_detail = f"找不到商品表：{cfg.project.table}"
        return report

    table = SheetTable.from_config(cfg)
    try:
        mismatches = table.validate_headers()
        rows = list(
            table.iter_rows(limit=limit, row_key=row_key, row_index=row_index, skip_done=skip_done)
        )
    except Exception as exc:  # noqa: BLE001
        report.table_detail = f"读取失败：{exc}"
        return report

    if mismatches:
        report.table_detail = "表头与列契约不一致：\n      - " + "\n      - ".join(mismatches)
        return report
    if not rows:
        report.table_detail = "筛选后一行都没有（需要「文件夹路径」+「主图」都填了才算一行）"
        return report
    report.table_ok = True
    report.table_detail = f"{Path(cfg.project.table).name}｜{len(rows)} 行"

    # 图片文件夹：命令行给了就用它；否则看表里第 1 列
    rows_folders = {str(Path(r.folder)) for r in rows if r.folder}
    folder: Path | None
    if images_dir:
        folder = Path(images_dir)
    elif len(rows_folders) == 1:
        folder = Path(next(iter(rows_folders)))
    else:
        folder = None

    cache: dict[Path, FolderIndex] = {}
    if folder is not None:
        ok, detail, index, count = check_folder(folder)
        report.images_dir = folder
        report.folder_ok = ok
        report.folder_detail = detail
        report.image_count = count
        if index is not None:
            cache[folder] = index
    elif not rows_folders:
        report.folder_ok = False
        report.folder_detail = "表里第 1 列「文件夹路径」是空的，也没给 --images-dir"
        return report
    else:
        # 多行分属不同文件夹：不做全局判定，交给逐行各查各的
        report.folder_ok = True
        report.folder_detail = f"各行分属 {len(rows_folders)} 个文件夹，逐行检查"

    report.rows = check_rows(rows, images_dir=images_dir, index_cache=cache)
    if folder is None:
        report.image_count = sum(len(idx.files) for idx in cache.values())
    return report
