"""把刚上架的商品读回来对一遍（只读）。

核对四件事：中文标题、英文标题、轮播/缩略图、素材图 —— 都得是**这一行的那几张**，
不能还挂着模板的图。

用法：
    python tools\\verify_products.py hat-tshirt            # 核对 live_runs.jsonl 里跑过的行
    python tools\\verify_products.py hat-tshirt 220114595101444742
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from temu_bot.config import load_config
from temu_bot.dianxiaomi.client import DxmClient
from temu_bot.imaging.swap import describe_product_images
from temu_bot.listing import get_en_title
from temu_bot.sheet.table import SheetTable

argv = sys.argv[1:]
login = argv[0] if argv else "hat-tshirt"
explicit = [a for a in argv[1:] if not a.startswith("-")]

cfg = load_config()
table_rows = {r.row_index: r for r in SheetTable.from_config(cfg).iter_rows()}

pairs: list[tuple[int | None, str]] = []
if explicit:
    pairs = [(None, pid) for pid in explicit]
else:
    log = Path("data/state/live_runs.jsonl")
    if not log.exists():
        raise SystemExit(f"没有 {log}，请直接把商品 id 当参数传进来")
    for line in log.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        if item.get("ok") and item.get("product_id"):
            pairs.append((item["row_index"], item["product_id"]))

print(f"登录身份 {login}｜核对 {len(pairs)} 个商品\n")

problems: list[str] = []
with DxmClient(cfg, login) as client:
    for index, product_id in pairs:
        response = client.product_edit(product_id)
        product = (response.get("data") or {}).get("product") or {}
        if not product:
            problems.append(f"{product_id}：读不到商品")
            print(f"❌ {product_id} 读不到商品")
            continue

        row = table_rows.get(index) if index else None
        info = describe_product_images(product)
        got_cn = str(product.get("productName") or "")
        got_en = get_en_title(product)

        print(f"{'第 ' + str(index) + ' 行' if index else '     '}  商品 {product_id}")
        print(f"   中文标题  {got_cn}")
        print(f"   英文标题  {got_en}")
        print(f"   轮播      {info['preview_count']} 张")
        for url in str(info["preview_first"]).split("|"):
            print(f"             {url}")
        print(f"   缩略图    共 {info['thumb_count']} 种：{info['preview_first'].split('|')[0]}")
        print(f"   素材图    {info['material']}")

        if row:
            if got_cn != row.title_cn:
                problems.append(f"第 {index} 行 中文标题不符：{got_cn!r} != {row.title_cn!r}")
            if row.title_en and got_en != row.title_en:
                problems.append(f"第 {index} 行 英文标题不符：{got_en!r} != {row.title_en!r}")
            # 图片必须指向本次上传的图（cosUrl 落在店小秘的图片域名下），
            # 不能还是模板的图（落在 img.cdnfe.com/product/open 下）
            for label, value in (
                ("轮播", info["preview_first"]),
                ("素材图", info["material"]),
            ):
                for url in str(value).split("|"):
                    if not url:
                        continue
                    if "dianxiaomi.com" not in url:
                        problems.append(f"第 {index} 行 {label} 还是模板的图：{url}")
        else:
            problems.append(f"{product_id}：日志里没记行号，只做了图片检查")

        print()

print("=" * 72)
if problems:
    print(f"❌ 有 {len(problems)} 处不对：")
    for p in problems:
        print(f"   · {p}")
    raise SystemExit(1)
print("✅ 全部核对通过：标题与图片都是表里这一行的")
