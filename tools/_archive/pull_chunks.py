"""把主包里引用到的所有 chunk 从静态资源域名批量拉下来（JS 逆向用）。

主包 index-*.js 里的 `import("./index-XXXX.js")` 和 __vite__mapDeps 数组包含全部子包名，
静态资源在 https://s13.dianxiaomi.com/dxm-web/<版本>/assets/<name>，直接 GET 即可（不需要登录）。
"""

from __future__ import annotations

import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

ASSET_VERSION = "2026-10"
ASSET_BASE = f"https://s13.dianxiaomi.com/dxm-web/{ASSET_VERSION}/assets"
OUT_DIR = Path(r"D:\temu_bot\data\reports\js_all")
NAME_RE = re.compile(r'"([A-Za-z0-9_\.\-]+\.js)"|import\("\./([A-Za-z0-9_\.\-]+\.js)"\)')


def main() -> None:
    main_bundle = OUT_DIR / "index-DyY2dwsZ.js"
    if not main_bundle.exists():
        print(f"找不到主包 {main_bundle}，先跑 pull_app_js.py")
        return

    text = main_bundle.read_text(encoding="utf-8", errors="replace")
    names: set[str] = set()
    for m in NAME_RE.finditer(text):
        name = m.group(1) or m.group(2)
        if name and not name.startswith("http"):
            names.add(name)

    # 补上路由里直接写死的
    for m in re.finditer(r'"(index-[A-Za-z0-9_\-]+\.js)"', text):
        names.add(m.group(1))

    todo = sorted(n for n in names if not (OUT_DIR / n).exists())
    print(f"主包引用到的 chunk 共 {len(names)} 个，本地缺失 {len(todo)} 个，开始下载…")

    ok = fail = 0
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        def fetch(name: str) -> tuple[str, int]:
            try:
                r = client.get(f"{ASSET_BASE}/{name}")
                if r.status_code == 200 and r.text:
                    (OUT_DIR / name).write_text(r.text, encoding="utf-8")
                    return name, len(r.text)
                return name, -r.status_code
            except Exception as exc:  # noqa: BLE001
                return name, -1

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(fetch, n) for n in todo]
            for i, fut in enumerate(as_completed(futures), 1):
                name, size = fut.result()
                if size > 0:
                    ok += 1
                else:
                    fail += 1
                if i % 50 == 0:
                    print(f"  …{i}/{len(todo)}  成功 {ok} 失败 {fail}")

    print(f"完成：成功 {ok}，失败 {fail} → {OUT_DIR}")


if __name__ == "__main__":
    main()
