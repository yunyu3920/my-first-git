"""测试夹具：全部在 tmp_path 沙箱里造表，绝不碰用户的真实商品表。"""

from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest

from temu_bot.config import Config
from temu_bot.sheet.schema import EXPECTED_HEADER

HEADER_ROW = [EXPECTED_HEADER[i] for i in sorted(EXPECTED_HEADER)]


def make_table(path: Path, rows: list[dict[int, object]]) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(HEADER_ROW)
    for row in rows:
        cells = [""] * len(HEADER_ROW)
        for col, value in row.items():
            cells[col - 1] = value
        ws.append(cells)
    wb.save(path)
    return path


@pytest.fixture()
def sample_rows() -> list[dict[int, object]]:
    return [
        {
            1: str(Path("C:/tmp/shop_a")),
            2: "00742093597.jpg",
            8: "00742093597-素材图.jpg",
            9: "00742093597",
            10: "A lovely costume",
            11: "8909735440",
            12: 1,
            13: "184807703165778961",
            14: "成功",
            17: "男士休闲图案印花短袖T恤夏季",
            18: "Men's T-Shirt Casual Graphic Print Short Sleeve Summer",
        },
        {
            1: str(Path("C:/tmp/shop_a")),
            2: "26746907929.jpg",
            8: "26746907929-素材图.jpg",
            9: "26746907929",
            10: "A lovely costume",
            11: "8909735440",
            12: 1,
            13: "184807703165779045",
            14: "失败",
            17: "男士短袖T恤眨眼驯鹿圣诞文字印花红黑格纹烫金节日出游派对",
            18: "Men's T-Shirt Blinking Reindeer Christmas Text Print",
        },
        # 只有文件夹、没有主图 → 不算有效数据行
        {1: str(Path("C:/tmp/shop_a")), 10: "A lovely costume"},
    ]


@pytest.fixture()
def row(tmp_path: Path):
    """一行"真能跑"的商品数据：文件夹和图片都真的存在、格式合规。

    放在 conftest 里，别的测试模块也能用（以前只在 `test_listing.py` 里，
    结果 `test_writeback.py` 只好自己重造了一份）。
    """
    from PIL import Image

    from temu_bot.sheet.schema import SheetRow, make_row_key

    folder = tmp_path / "imgs"
    folder.mkdir()
    # 主图 1340x1785（合规，直通）、素材图 800x800（合规）
    Image.new("RGB", (1340, 1785), (180, 40, 40)).save(folder / "00742093597.jpg", quality=90)
    Image.new("RGB", (800, 800), (40, 40, 180)).save(
        folder / "00742093597-素材图.jpg", quality=90
    )

    return SheetRow(
        row_index=2,
        row_key=make_row_key(str(folder), "00742093597.jpg", "8909735440"),
        folder=str(folder),
        images={"main": ["00742093597.jpg"], "material": ["00742093597-素材图.jpg"]},
        shop="A hat tshirt",
        template_spu="8909735440",
        main_image_index=1,
        title_cn="女士长袖T恤 圣诞雪人印花",
        title_en="Women Long Sleeve T-Shirt Snowman Print",
        # 第 9 列：SKU 货号前缀编码（上架时要填进每个 SKU 的 extCode）
        raw={9: "00742093597"},
    )


@pytest.fixture()
def cfg_with_shop(sandbox):
    """配置里有两个店（对应两个登录身份）的沙箱配置。"""
    from temu_bot.config import ShopCfg

    cfg = sandbox["cfg"]
    cfg.shops = [
        ShopCfg(name="A hat tshirt", login="hat-tshirt", expected_dxm_shops=["A hat tshirt"]),
        ShopCfg(
            name="A lovely costume", login="lovely", expected_dxm_shops=["A lovely costume"]
        ),
    ]
    return cfg


@pytest.fixture()
def sandbox(tmp_path: Path, sample_rows) -> dict[str, object]:
    table = make_table(tmp_path / "商品信息表.xlsx", sample_rows)
    cfg = Config(
        project={"table": table, "sheet_name": "Sheet1"},
        toolbox={"app_dir": tmp_path / "toolbox", "ledger": tmp_path / "toolbox" / "used_ids.txt"},
    )
    cfg.root = tmp_path
    cfg.data_dir = tmp_path / "data"
    cfg.work_dir = tmp_path / "work"
    cfg.ensure_dirs()
    return {"cfg": cfg, "table": table, "tmp": tmp_path}
