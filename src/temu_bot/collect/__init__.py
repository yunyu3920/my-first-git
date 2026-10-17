"""收集图片：把一个文件夹里的图整理成一张「商品信息表」。

约定：`ID.jpg` 是主图，`ID-素材图.jpg` 是它的素材图，一对图 = 一行商品。
缺素材图的行默认**跳过**（用户明确要求），勾了「强制生成素材图」才用主图生成一张。

产出**永远是新文件**——目标已存在会自动加时间戳另存，不会覆盖。
"""

from temu_bot.collect.scan import (
    MATERIAL_SUFFIX,
    CollectOptions,
    ScanResult,
    ScanRow,
    Skipped,
    check_main_images,
    find_unknown_shop,
    is_material_name,
    main_image_candidates,
    row_cells,
    scan_folder,
)
from temu_bot.collect.titles import (
    TitleEntry,
    TitleList,
    load_title_list,
    normalize_key,
)
from temu_bot.collect.writer import (
    DEFAULT_NAME,
    WriteResult,
    default_output,
    safe_output_path,
    write_table,
)

__all__ = [
    "DEFAULT_NAME",
    "MATERIAL_SUFFIX",
    "CollectOptions",
    "ScanResult",
    "ScanRow",
    "Skipped",
    "TitleEntry",
    "TitleList",
    "WriteResult",
    "check_main_images",
    "default_output",
    "find_unknown_shop",
    "is_material_name",
    "load_title_list",
    "main_image_candidates",
    "normalize_key",
    "row_cells",
    "safe_output_path",
    "scan_folder",
    "write_table",
]
