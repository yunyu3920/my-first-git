"""让浏览器把页面实际加载的 JS 交出来，然后在里面找指定接口的调用代码。

用途：店小秘的接口调用分散在按需加载的 chunk 里（主包只有路由壳）。
把它真正加载过的 JS 全抓下来搜，比猜文件名靠谱。

用法:
    python tools\\hunt_api_js.py <页面URL> <关键词1> [关键词2 ...]
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from temu_bot.config import ShopCfg, load_config
from temu_bot.browser.playwright_local import PlaywrightLocalProvider

MAX_JS = 60
CONTEXT = 700


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        return
    url = sys.argv[1]
    keywords = sys.argv[2:]

    cfg = load_config()
    shop = ShopCfg(name="__hunt__", profile_dir=cfg.root / "profiles" / "__hunt__")
    out_dir = cfg.data_dir / "reports" / "js_hunt"
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
        time.sleep(4)

        print(f"页面实际加载 JS {len(js_urls)} 个")
        for u in js_urls[:MAX_JS]:
            print("   ", u)

        api = context.request
        hits_total: dict[str, int] = {}
        saved: list[str] = []

        for js_url in js_urls[:MAX_JS]:
            try:
                body = api.get(js_url).text()
            except Exception as exc:  # noqa: BLE001
                print(f"  下载失败 {js_url}: {exc}")
                continue

            found = {k: body.count(k) for k in keywords if k in body}
            if not found:
                continue

            print(f"\n*** 命中 {js_url}  {found}")
            hits_total.update(found)

            name = js_url.rsplit("/", 1)[-1]
            saved.append(name)
            (out_dir / name).write_text(body, encoding="utf-8")

            for kw in found:
                index = 0
                shown = 0
                while shown < 3:
                    index = body.find(kw, index)
                    if index < 0:
                        break
                    start = max(0, index - CONTEXT // 2)
                    print(f"  --- {kw} @{index} ---")
                    print("  " + body[start:index + CONTEXT].replace("\n", " "))
                    index += len(kw)
                    shown += 1

    print("\n=== 命中汇总 ===", json.dumps(hits_total, ensure_ascii=False))
    print("已保存的 JS:", saved)


if __name__ == "__main__":
    main()
