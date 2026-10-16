"""商品信息表的列契约（1-based 列号，已对照真实文件核实）。

真实文件：C:\\Users\\A\\Desktop\\Temu\\商品信息表.xlsx
  - 单 sheet `Sheet1`，表头第 1 行，20 列。
  - 图片列每格只放一个文件名（不带路径，目录由第 1 列「文件夹路径」给出）。
  - 现有数据无「一格多文件名」样本，因此分隔符可配（见 SheetCfg）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from hashlib import sha1
from pathlib import Path
from typing import Any, Iterable

# ------------------------------------------------------------------ 列号
COL_FOLDER = 1            # 文件夹路径
COL_MAIN_IMAGE = 2        # 主图
COL_SKU_IMAGE = 3         # SKU图片（预留，本期不用）
COL_DETAIL_IMAGE = 4      # 详情图（预留）
COL_PREVIEW_IMAGE = 5     # 预览图（预留）
COL_IMAGE_PACK = 6        # 图片包（预留）
COL_PACKAGE_IMAGE = 7     # 包装图片（预留）
COL_MATERIAL_IMAGE = 8    # 素材图
COL_SKU_PREFIX_CODE = 9   # 一键生成SKU货号前缀编码（= 主图名里的 11 位 ID）
COL_SHOP = 10             # 店铺名
COL_TEMPLATE_SPU = 11     # 模板产品SPUID
COL_MAIN_IMAGE_INDEX = 12 # 主图索引（现全为 1）
COL_PRODUCT_ID = 13       # 商品ID（回写）
COL_STATUS = 14           # 状态（回写）
COL_UNIQUE_KEY = 15       # 提取唯一标识符（回写）
COL_PROBLEM = 16          # 问题项（回写）
COL_TITLE_CN = 17         # 中文标题
COL_TITLE_EN = 18         # 英文标题
COL_SKC_CODE = 19         # SKC货号（回写）
COL_VIDEO = 20            # 视频路径

EXPECTED_HEADER: dict[int, str] = {
    COL_FOLDER: "文件夹路径",
    COL_MAIN_IMAGE: "主图",
    COL_SKU_IMAGE: "SKU图片",
    COL_DETAIL_IMAGE: "详情图",
    COL_PREVIEW_IMAGE: "预览图",
    COL_IMAGE_PACK: "图片包",
    COL_PACKAGE_IMAGE: "包装图片",
    COL_MATERIAL_IMAGE: "素材图",
    COL_SKU_PREFIX_CODE: "一键生成SKU货号前缀编码",
    COL_SHOP: "店铺名",
    COL_TEMPLATE_SPU: "模板产品SPUID",
    COL_MAIN_IMAGE_INDEX: "主图索引",
    COL_PRODUCT_ID: "商品ID",
    COL_STATUS: "状态",
    COL_UNIQUE_KEY: "提取唯一标识符",
    COL_PROBLEM: "问题项",
    COL_TITLE_CN: "中文标题",
    COL_TITLE_EN: "英文标题",
    COL_SKC_CODE: "SKC货号",
    COL_VIDEO: "视频路径",
}

# 图片列 → 逻辑图位名（用于 images dict）
IMAGE_SLOTS: dict[int, str] = {
    COL_MAIN_IMAGE: "main",
    COL_SKU_IMAGE: "sku",
    COL_DETAIL_IMAGE: "detail",
    COL_PREVIEW_IMAGE: "preview",
    COL_IMAGE_PACK: "pack",
    COL_PACKAGE_IMAGE: "package",
    COL_MATERIAL_IMAGE: "material",
}

SLOT_COLUMNS: dict[str, int] = {v: k for k, v in IMAGE_SLOTS.items()}

# 读输入 / 写回写
INPUT_COLUMNS: tuple[int, ...] = (
    COL_FOLDER,
    COL_MAIN_IMAGE,
    COL_MATERIAL_IMAGE,
    COL_SHOP,
    COL_TEMPLATE_SPU,
    COL_MAIN_IMAGE_INDEX,
    COL_TITLE_CN,
    COL_TITLE_EN,
)
WRITE_COLUMNS: tuple[int, ...] = (
    COL_PRODUCT_ID,
    COL_STATUS,
    COL_UNIQUE_KEY,
    COL_PROBLEM,
    COL_SKC_CODE,
)

# 中文标题列上限（仅用于预检查告警；Temu 英文上限走 ai.title_max_len）
TITLE_CN_SOFT_LIMIT = 60


# ------------------------------------------------------------------ 工具函数
def normalize_cell(value: Any) -> str:
    """单元格 → 干净字符串（去首尾空白，浮点整数化，None → ""）。"""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def split_multi(value: str, separator: str, fallback_pattern: str = "") -> list[str]:
    """把一格里的多个文件名拆开。

    先用配置的分隔符；只有一个片段时再用兜底正则（逗号/分号/竖线等）。
    现有真实数据每格只有 1 个文件名，所以这条路径主要面向未来的多图场景。
    """
    value = (value or "").strip()
    if not value:
        return []

    parts: list[str]
    if separator and separator in value:
        parts = value.split(separator)
    else:
        parts = [value]

    cleaned = [p.strip().strip('"').strip() for p in parts if p and p.strip()]

    if len(cleaned) <= 1 and fallback_pattern:
        alt = [p.strip() for p in re.split(fallback_pattern, value) if p.strip()]
        if len(alt) > 1:
            cleaned = alt

    return cleaned


def make_row_key(folder: str, main_image: str, template_spu: str) -> str:
    """幂等键：不依赖行号（插行不会错位）。"""
    raw = f"{folder.strip()}|{main_image.strip()}|{template_spu.strip()}"
    return sha1(raw.encode("utf-8")).hexdigest()[:16]


def header_mismatches(header: dict[int, str]) -> list[str]:
    """比对表头与契约，返回人类可读的差异列表（空 = 完全一致）。"""
    problems: list[str] = []
    for col, expected in EXPECTED_HEADER.items():
        actual = normalize_cell(header.get(col, ""))
        if actual != expected:
            problems.append(f"第 {col} 列：期望「{expected}」，实际「{actual or '（空）'}」")
    extra = [c for c in header if c > max(EXPECTED_HEADER)]
    if extra:
        problems.append(f"多出列号 {sorted(extra)}（契约外的新列，读时会忽略）")
    return problems


# ------------------------------------------------------------------ 行模型
@dataclass(slots=True)
class SheetRow:
    row_index: int                 # Excel 行号（1-based，含表头）
    row_key: str
    folder: str
    images: dict[str, list[str]]
    shop: str
    template_spu: str
    main_image_index: int | None
    title_cn: str
    title_en: str
    status: str = ""
    product_id: str = ""
    skc_code: str = ""
    problem: str = ""
    unique_key: str = ""
    raw: dict[int, str] = field(default_factory=dict)

    @property
    def main_image(self) -> str:
        got = self.images.get("main") or []
        return got[0] if got else ""

    @property
    def material_image(self) -> str:
        got = self.images.get("material") or []
        return got[0] if got else ""

    @property
    def sku_prefix_code(self) -> str:
        """第 9 列：SKU 货号前缀编码。现有数据 = 主图名去扩展名的 11 位 ID。"""
        return normalize_cell(self.raw.get(COL_SKU_PREFIX_CODE, ""))

    def folder_path(self) -> Path:
        return Path(self.folder)

    def image_path(self, slot: str, name: str) -> Path:
        return self.folder_path() / name

    def to_view(self) -> dict[str, Any]:
        """给 CLI / 控制台展示用的精简字典。"""
        return {
            "row": self.row_index,
            "row_key": self.row_key,
            "folder": self.folder,
            "main_image": self.main_image,
            "material_image": self.material_image,
            "sku_prefix_code": self.sku_prefix_code,
            "shop": self.shop,
            "template_spu": self.template_spu,
            "main_image_index": self.main_image_index,
            "title_cn": self.title_cn,
            "title_en": self.title_en,
            "status": self.status,
            "product_id": self.product_id,
        }


def build_row(
    row_index: int,
    cells: dict[int, str],
    *,
    separator: str,
    fallback_pattern: str,
) -> SheetRow | None:
    """从一行原始单元格构造 SheetRow；不是有效数据行则返回 None。"""
    folder = normalize_cell(cells.get(COL_FOLDER))
    main_image = normalize_cell(cells.get(COL_MAIN_IMAGE))
    template_spu = normalize_cell(cells.get(COL_TEMPLATE_SPU))

    # 有效数据行的最低要求：有文件夹 + 有主图
    if not folder or not main_image:
        return None

    images: dict[str, list[str]] = {}
    for col, slot in IMAGE_SLOTS.items():
        names = split_multi(normalize_cell(cells.get(col)), separator, fallback_pattern)
        if names:
            images[slot] = names

    idx_raw = normalize_cell(cells.get(COL_MAIN_IMAGE_INDEX))
    try:
        main_index = int(idx_raw) if idx_raw else None
    except ValueError:
        main_index = None

    return SheetRow(
        row_index=row_index,
        row_key=make_row_key(folder, main_image, template_spu),
        folder=folder,
        images=images,
        shop=normalize_cell(cells.get(COL_SHOP)),
        template_spu=template_spu,
        main_image_index=main_index,
        title_cn=normalize_cell(cells.get(COL_TITLE_CN)),
        title_en=normalize_cell(cells.get(COL_TITLE_EN)),
        status=normalize_cell(cells.get(COL_STATUS)),
        product_id=normalize_cell(cells.get(COL_PRODUCT_ID)),
        skc_code=normalize_cell(cells.get(COL_SKC_CODE)),
        problem=normalize_cell(cells.get(COL_PROBLEM)),
        unique_key=normalize_cell(cells.get(COL_UNIQUE_KEY)),
        raw={k: normalize_cell(v) for k, v in cells.items()},
    )


def iter_missing_files(row: SheetRow, slots: Iterable[str] = ("main", "material")) -> list[str]:
    """预检查：返回缺失的图片文件名列表。"""
    missing: list[str] = []
    folder = row.folder_path()
    for slot in slots:
        for name in row.images.get(slot, []):
            if not (folder / name).exists():
                missing.append(name)
    return missing
