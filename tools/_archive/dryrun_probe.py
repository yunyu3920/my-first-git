"""端到端干跑验证（不写任何数据、不扣米豆）。

表里 98 行全是「A lovely costume」，而目前只有 hat-tshirt 登录态，
所以拿 hat-tshirt 账号里**真实存在**的一个 SPU 造一行临时数据，
把 找模板 → 复制(干跑) → 改标题(干跑) 这半段也真跑一遍。

用法： .venv\\Scripts\\python.exe tools\\dryrun_probe.py [SPU] [登录身份]
"""

from __future__ import annotations

import sys
from pathlib import Path

from rich.console import Console

from temu_bot.config import load_config
from temu_bot.listing import ListingPipeline
from temu_bot.sheet.schema import SheetRow, make_row_key

console = Console()

SPU = sys.argv[1] if len(sys.argv) > 1 else "52511674869"
LOGIN = sys.argv[2] if len(sys.argv) > 2 else "hat-tshirt"

FOLDER = r"C:\Users\A\Desktop\Temu"
MAIN = "00742093597.jpg"
MATERIAL = "00742093597-素材图.jpg"

cfg = load_config()

row = SheetRow(
    row_index=-1,
    row_key=make_row_key(FOLDER, MAIN, SPU),
    folder=FOLDER,
    images={"main": [MAIN], "material": [MATERIAL]},
    shop="A hat tshirt",
    template_spu=SPU,
    main_image_index=1,
    title_cn="男士短袖圆领T恤猫咪印花夏季休闲",
    title_en="Men's Short Sleeve Round Neck T-Shirt Cat Print Summer Casual",
)

for name in (MAIN, MATERIAL):
    p = Path(FOLDER, name)
    console.print(f"{'✅' if p.exists() else '❌'} 图片存在：{name}")

console.print(f"\n[bold]干跑探针[/bold] 登录态={LOGIN} 模板SPU={SPU}\n")

outcome = ListingPipeline(cfg, row, login=LOGIN, live=False).run()

for step in outcome.steps:
    console.print(step.line())

console.print()
console.print("[red]有失败步骤[/red]" if outcome.failed else "[green]全部通过（未写任何数据）[/green]")
