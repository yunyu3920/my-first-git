"""自测：页面侧的请求体钩子能不能真的抓到 multipart 里的 File 内容。

不碰店小秘——在本地页面里发一个带 File 的 FormData 到一个不存在的域名，
看钩子有没有把文件内容记下来。
"""

from __future__ import annotations

import json
import time

from temu_bot.browser.playwright_local import PlaywrightLocalProvider
from temu_bot.browser.recorder import REQUEST_HOOK_JS
from temu_bot.config import ShopCfg, load_config

PAYLOAD_JSON = '{"productName":"Women Long Sleeve T-Shirt","op":"1"}'


def main() -> None:
    cfg = load_config()
    cfg.browser.headless = True
    shop = ShopCfg(name="__hooktest__", profile_dir=cfg.root / "profiles" / "__hooktest__")

    with PlaywrightLocalProvider().launch(cfg, shop) as session:
        context = session.context()
        context.add_init_script(REQUEST_HOOK_JS)
        page = session.page()
        page.set_content("<h1>hook test</h1>")
        page.evaluate(REQUEST_HOOK_JS)

        page.evaluate(
            """
            (payload) => {
              const fd = new FormData();
              fd.append('file', new File([payload], 'choiceSave.txt', {type: 'text/plain'}));
              fd.append('op', '1');
              const xhr = new XMLHttpRequest();
              xhr.open('POST', 'https://example.invalid/api/popTemuProduct/add3f8c2a97.json');
              try { xhr.send(fd); } catch (e) {}
            }
            """,
            PAYLOAD_JSON,
        )
        time.sleep(2)   # 钩子里读 File 是异步的
        captured = page.evaluate("window.__dxmCaptured || []")
        blobs = page.evaluate("window.__dxmBlobs || []")

    print(f"文本捕获 {len(captured)} 条，二进制捕获 {len(blobs)} 个")

    ok = False
    for item in captured:
        body = item.get("body") or ""
        print("  url:", item.get("url"))
        print("  body 前 200 字符:")
        print("   ", body[:200].replace("\n", "\n    "))
        if "op=1" in body:
            ok = True

    # 二进制部分必须能 base64 还原回原文
    import base64

    for blob in blobs:
        raw = base64.b64decode(blob.get("base64") or "")
        print(f"  blob: field={blob.get('field')} filename={blob.get('filename')} size={blob.get('size')}")
        print("        还原后内容:", raw.decode("utf-8", "replace")[:120])
        if raw.decode("utf-8", "replace") == PAYLOAD_JSON:
            ok = True

    print()
    print("结果:", "✅ 钩子能无损抓到 multipart 内容（含二进制）" if ok else "❌ 没抓到，需要换方案")
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
