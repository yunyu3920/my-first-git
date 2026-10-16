"""图片管线：按 Temu 规格出图，原图只读。"""

from temu_bot.imaging.pipeline import (
    PASS,
    PrepResult,
    PlannedImage,
    out_dir_for,
    plan_row,
    process_row,
)
from temu_bot.imaging.resolve import (
    AMBIGUOUS,
    EXACT,
    HOW_LABELS,
    MISSING,
    FolderIndex,
    Resolved,
    index_folder,
)
from temu_bot.imaging.specs import (
    MAIN_IMAGE,
    MATERIAL_IMAGE,
    SPECS,
    ImageInfo,
    ImageSpec,
    inspect,
    problems_of,
)

__all__ = [
    "AMBIGUOUS",
    "EXACT",
    "HOW_LABELS",
    "MAIN_IMAGE",
    "MATERIAL_IMAGE",
    "MISSING",
    "PASS",
    "SPECS",
    "FolderIndex",
    "ImageInfo",
    "ImageSpec",
    "PlannedImage",
    "PrepResult",
    "Resolved",
    "index_folder",
    "inspect",
    "out_dir_for",
    "plan_row",
    "problems_of",
    "process_row",
]
