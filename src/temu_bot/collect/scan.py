"""收集图片：把一个文件夹里的图，整理成一张「商品信息表」。

约定（来自用户实际的素材目录 `C:\\Users\\A\\Desktop\\Temu`）：

    00742093597.jpg          ← 主图
    00742093597-素材图.jpg    ← 素材图（同名的，加 `-素材图` 后缀）

一对图 = 一行商品。所以：

- 主图      = 目录里**不带** `-素材图` 后缀的图片
- 素材图    = 同名 + `-素材图` 后缀的那张
- 前缀编码  = 主图去掉扩展名的名字（第 9 列）
- 商品ID / 状态 / 中英标题 = 留空，等上架后再回填

**缺素材图默认跳过该行**（用户明确要求），并如实报告跳过了哪些。
勾了「强制生成素材图」才改成用主图生成一张。

素材图是按**名字去目录里找**的（`imaging/resolve`），所以大小写不同、
扩展名不同（.jpeg/.png）也认得出来。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from temu_bot.imaging.resolve import FolderIndex, index_folder
from temu_bot.imaging.specs import ImageSpec, inspect
from temu_bot.sheet import schema

log = logging.getLogger(__name__)

# 素材图的后缀（主图名 + 这个后缀 = 素材图名）
MATERIAL_SUFFIX = "-素材图"

# 生成素材图时的目标（用户表里素材图都是 800x800）
DEFAULT_MATERIAL_SIZE = (800, 800)


@dataclass(slots=True)
class CollectOptions:
    folder: Path
    shop: str
    template_spu: str
    main_image_index: int = 1
    use_img_name_as_skc: bool = False
    force_gen_material: bool = False
    material_size: tuple[int, int] = DEFAULT_MATERIAL_SIZE
    # 是否连子文件夹里的图一起收集。默认**不**——
    # 一是符合"收集当前所选文件夹的图片"的语义，二是选到父目录时递归会慢很多。
    recursive: bool = False


@dataclass(slots=True)
class ScanRow:
    """扫描出来的一行（还没写进表）。"""

    index: int
    main_name: str
    material_name: str = ""
    stem: str = ""
    note: str = ""
    generated_material: bool = False

    @property
    def sku_prefix(self) -> str:
        return self.stem


@dataclass(slots=True)
class Skipped:
    name: str
    reason: str


@dataclass(slots=True)
class ScanResult:
    options: CollectOptions
    rows: list[ScanRow] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    image_count: int = 0
    # 建好的目录索引。留给调用方复用——不带上它的话，后面的主图规格检查
    # 会把整个目录**再扫一遍**（实测占了总耗时的一半以上）。
    index: FolderIndex | None = None

    @property
    def ok(self) -> bool:
        return bool(self.rows)

    def summary(self) -> str:
        where = "（含子文件夹）" if self.options.recursive else ""
        parts = [f"共 {self.image_count} 张图{where}"]
        parts.append(f"收成 {len(self.rows)} 行")
        if self.skipped:
            parts.append(f"跳过 {len(self.skipped)} 张")
        return "；".join(parts)


def is_material_name(name: str) -> bool:
    """这个名字是不是"素材图"（主图名 + `-素材图`）。"""
    return Path(name).stem.endswith(MATERIAL_SUFFIX)


def main_image_candidates(index: FolderIndex) -> list[Path]:
    """目录里的主图候选：不带 `-素材图` 后缀的图片，按文件名升序。"""
    files = [p for p in index.files if not is_material_name(p.name)]
    return sorted(files, key=lambda p: p.name.lower())


def _find_material(index: FolderIndex, stem: str, main_suffix: str = "") -> Path | None:
    """找 `<主图名>-素材图` 那张图（扩展名不同也认，但不做前缀猜测）。

    这里**必须**用 `find_by_stem` 而不是 `resolve`：`resolve` 的前缀策略会把主图
    `123.jpg` 当成 `123-素材图` 的匹配，导致"缺素材图"的行被误判成找到了。
    """
    got = index.find_by_stem(f"{stem}{MATERIAL_SUFFIX}", prefer_suffix=main_suffix)
    return got.path if got.ok else None


def generate_material(main: Path, dst: Path, size: tuple[int, int]) -> bool:
    """从主图生成一张素材图（等比放入 + 补白边，不裁掉图案）。"""
    from temu_bot.imaging.pipeline import save_under_limit
    from temu_bot.imaging.specs import MATERIAL_IMAGE

    try:
        with Image.open(main) as opened:
            image = opened.convert("RGB")
            image.thumbnail(size, Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", size, (255, 255, 255))
            canvas.paste(image, ((size[0] - image.width) // 2, (size[1] - image.height) // 2))
            spec = ImageSpec(
                key="material",
                label="素材图",
                width=size[0],
                height=size[1],
                fit="contain",
                max_bytes=MATERIAL_IMAGE.max_bytes,
                grounded=False,
            )
            save_under_limit(canvas, dst, spec)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("从 %s 生成素材图失败：%s", main.name, exc)
        return False


def scan_folder(options: CollectOptions, *, index: FolderIndex | None = None) -> ScanResult:
    """只读扫描：算出会收成哪些行、跳过哪些。**不写任何文件。**"""
    folder = Path(options.folder)
    result = ScanResult(options=options)

    if not folder.exists():
        result.skipped.append(Skipped(name="", reason=f"文件夹不存在：{folder}"))
        return result
    if not folder.is_dir():
        result.skipped.append(Skipped(name="", reason=f"这不是一个文件夹：{folder}"))
        return result

    idx = index if index is not None else index_folder(folder, recursive=options.recursive)
    result.index = idx
    result.image_count = len(idx.files)
    if not idx.files:
        result.skipped.append(Skipped(name="", reason="文件夹里一张图都没有"))
        return result

    for main in main_image_candidates(idx):
        stem = main.stem
        material = _find_material(idx, stem, main.suffix)

        if material is None:
            if not options.force_gen_material:
                # 用户明确要求：缺素材图就跳过这一行
                result.skipped.append(Skipped(name=main.name, reason="没有对应的素材图"))
                continue
            # 勾了"强制生成"：届时用主图生成
            row = ScanRow(
                index=len(result.rows) + 1,
                main_name=main.name,
                material_name=f"{stem}{MATERIAL_SUFFIX}.jpg",
                stem=stem,
                note="素材图将由主图生成",
                generated_material=True,
            )
        else:
            row = ScanRow(
                index=len(result.rows) + 1,
                main_name=main.name,
                material_name=material.name,
                stem=stem,
            )
        result.rows.append(row)

    return result


def check_main_images(
    result: ScanResult, *, index: FolderIndex | None = None, spec=None
) -> dict[str, list[str]]:
    """顺带看一眼主图合不合规（给页面提示用，不影响收集本身）。"""
    from temu_bot.imaging.specs import MAIN_IMAGE

    spec = spec or MAIN_IMAGE
    folder = Path(result.options.folder)
    # 优先用扫描时已经建好的索引，别把整个目录再走一遍
    if index is None:
        index = result.index
    if index is None:
        index = index_folder(folder, recursive=result.options.recursive)

    problems: dict[str, list[str]] = {}
    for row in result.rows:
        found = index.resolve(row.main_name)
        if not found.ok:
            problems[row.main_name] = ["找不到这张图"]
            continue
        info = inspect(found.path, spec)
        if info.problems:
            problems[row.main_name] = list(info.problems)
    return problems


def find_unknown_shop(cfg, shop_name: str) -> str:
    """填的店铺名在配置里找不到就返回一句提醒（空字符串 = 没问题）。

    为什么值得提醒：收集时写的「店铺名」会一路带进表里，上架时靠它核对当前登录态。
    名字写错 → 上架时被安全闸拦下，但你得先知道错在哪、正确写法是什么。
    """
    name = (shop_name or "").strip()
    if not name:
        return "还没填店铺名；上架时要靠它对账，建议现在填上。"
    known = [s.name for s in getattr(cfg, "shops", [])]
    if not known:
        return ""
    if name in known:
        return ""
    return f"「{name}」不在配置的店铺列表里（现有：{'、'.join(known)}）。上架时会被店铺核对拦下。"


def row_cells(row: ScanRow, options: CollectOptions) -> dict[int, object]:
    """把一行转成表格的单元格（列号 → 值）。列号见 sheet/schema.py。"""
    cells: dict[int, object] = {
        schema.COL_FOLDER: str(options.folder),
        schema.COL_MAIN_IMAGE: row.main_name,
        schema.COL_MATERIAL_IMAGE: row.material_name,
        schema.COL_SKU_PREFIX_CODE: row.sku_prefix,
        schema.COL_SHOP: options.shop,
        schema.COL_TEMPLATE_SPU: options.template_spu,
        schema.COL_MAIN_IMAGE_INDEX: options.main_image_index,
        # 商品ID / 状态 / 中英标题 / 唯一标识符 / 问题项 留给上架阶段回填
        schema.COL_PRODUCT_ID: "",
        schema.COL_STATUS: "",
        schema.COL_TITLE_CN: "",
        schema.COL_TITLE_EN: "",
        schema.COL_UNIQUE_KEY: "",
        schema.COL_PROBLEM: "",
        schema.COL_VIDEO: "",
    }
    cells[schema.COL_SKC_CODE] = row.sku_prefix if options.use_img_name_as_skc else ""
    return cells
