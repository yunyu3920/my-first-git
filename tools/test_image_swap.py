"""验证「换图」能不能真的生效（只改素材图一个字段，op=1 仅保存，不发布）。

用法：
    python tools\\test_image_swap.py                          # 用当前表第 2 行
    python tools\\test_image_swap.py --row-index 3            # 换一行
    python tools\\test_image_swap.py --product 1848… --image "D:\\x.jpg"
    python tools\\test_image_swap.py --restore                # 还原成原来的地址

商品ID 与素材图**默认从当前商品信息表里读**（第 13 列商品ID、第 1 列文件夹 +
第 8 列素材图），所以不用手抄。只动 materialImgUrl 一个字段，op=1（仅保存），
不复制、不发布。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from temu_bot.config import load_config
from temu_bot.dianxiaomi.client import OP_SAVE, DxmClient
from temu_bot.sheet.table import SheetTable

BACKUP = Path("data/state/material_before.json")


def arg(name: str, default: str | None = None) -> str | None:
    """取 `--name value` 或 `--name=value`。"""
    argv = sys.argv[1:]
    for i, item in enumerate(argv):
        if item == name and i + 1 < len(argv):
            return argv[i + 1]
        if item.startswith(name + "="):
            return item.split("=", 1)[1]
    return default


def main() -> int:
    restore = "--restore" in sys.argv
    login = arg("--login", "hat-tshirt") or "hat-tshirt"
    cfg = load_config()

    product_id = arg("--product")
    image = Path(arg("--image")) if arg("--image") else None

    if not product_id:
        index = int(arg("--row-index", "2") or 2)
        row = next(
            (r for r in SheetTable.from_config(cfg).iter_rows() if r.row_index == index),
            None,
        )
        if row is None:
            print(f"表里没有第 {index} 行，退出")
            return 1
        product_id = row.product_id.strip()
        image = row.folder_path() / row.material_image
        print(f"拿表里第 {index} 行：商品 {product_id or '(空)'}｜素材图 {image.name}")
    if not product_id:
        print("这一行没有商品ID，没法试换图，退出")
        return 1

    def read(client: DxmClient) -> dict:
        return ((client.product_edit(product_id).get("data") or {}).get("product")) or {}

    with DxmClient(cfg, login) as client:
        product = read(client)
        if not product:
            print("读不到商品，退出")
            return 1
        print(f"商品 {product_id}")
        print(f"  当前 materialImgUrl = {product.get('materialImgUrl')}")

        if restore:
            if not BACKUP.exists():
                print("没有备份文件，无法还原")
                return 1
            original = json.loads(BACKUP.read_text(encoding="utf-8"))["materialImgUrl"]
            product["materialImgUrl"] = original
            result = client.save_product(product, op=OP_SAVE)
            print(f"\n还原 → {original}")
            print(f"保存返回 code={result.get('code')} msg={result.get('msg')}")
            after = read(client)
            print(f"  还原后 materialImgUrl = {after.get('materialImgUrl')}")
            ok = after.get("materialImgUrl") == original
            print("\n" + ("✅ 还原成功" if ok else "❌ 还原失败"))
            return 0 if ok else 1

        if image is None or not Path(image).is_file():
            print(f"素材图不存在：{image}")
            return 1

        # 1) 备份原值
        BACKUP.parent.mkdir(parents=True, exist_ok=True)
        BACKUP.write_text(
            json.dumps({"materialImgUrl": product.get("materialImgUrl")}, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"  已备份原值到 {BACKUP}")

        # 2) 上传这张图
        print(f"\n上传 {Path(image).name} …")
        uploaded = client.upload_image(Path(image))
        cos_url = "https:" + uploaded.get("cosUrl", "") if uploaded.get("cosUrl") else ""
        print(f"  上传返回：{json.dumps(uploaded, ensure_ascii=False)[:200]}")
        print(f"  候选新地址：{cos_url}")

        # 3) 换上去，只保存
        product["materialImgUrl"] = cos_url
        result = client.save_product(product, op=OP_SAVE)
        print(f"\n保存（op=1 仅保存）返回 code={result.get('code')} msg={result.get('msg')}")

        # 4) 读回验证
        after = read(client)
        print(f"  读回 materialImgUrl = {after.get('materialImgUrl')}")
        ok = after.get("materialImgUrl") == cos_url
        print("\n" + ("✅ 换图生效了！" if ok else "❌ 没生效（写回去的地址没保留）"))
        if not ok:
            print("   原值已备份，可用 --restore 还原。")
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
