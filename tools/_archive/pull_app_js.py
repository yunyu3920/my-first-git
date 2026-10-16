"""把店小秘页面实际加载的**自家** JS（/dxm-web/ 的 chunk）全量拉到本地，便于反复搜索。

第三方库（/fe-res/extendPlugins/）跳过，体积大且无用。
拉下来的文件放 data/reports/js_all/，可以直接用 Grep 工具搜。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from temu_bot.config import ShopCfg, load_config
from temu_bot.browser.playwright_local import PlaywrightLocalProvider

DEFAULT_URL = "https://www.dianxiaomi.com/web/popTemu/pageList.json?dxmState=offline"


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL

    cfg = load_config()
    shop = ShopCfg(name="__pulljs__", profile_dir=cfg.root / "profiles" / "__pulljs__")
    out_dir = cfg.data_dir / "reports" / "js_all"
    out_dir.mkdir(parents=True, exist_ok=True)

    js_urls: list[str] = []
    with PlaywrightLocalProvider().launch(cfg, shop) as session:
        context = session.context()
        page = session.page()

        def on_response(response):
            u = response.url
            if u.endswith(".js") and u not in js_urls:
                js_urls.append(u)

        context.on("response", on_response)
        print(f"打开 {url}")
        page.goto(url, wait_until="networkidle", timeout=90_000)
        time.sleep(5)

        app_js = [u for u in js_urls if "/dxm-web/" in u and u.endswith(".js")]
        print(f"页面共加载 JS {len(js_urls)} 个，其中自家 chunk {len(app_js)} 个")

        api = context.request
        saved = 0
        total = 0
        for js_url in app_js:
            name = js_url.rsplit("/", 1)[-1]
            target = out_dir / name
            if target.exists():
                continue
            try:
                body = api.get(js_url).text()
            except Exception as exc:  # noqa: BLE001
                print(f"  下载失败 {name}: {exc}")
                continue
            target.write_text(body, encoding="utf-8")
            saved += 1
            total += len(body)

    print(f"新保存 {saved} 个文件，共 {total / 1024:.0f} KB → {out_dir}")


if __name__ == "__main__":
    main()
