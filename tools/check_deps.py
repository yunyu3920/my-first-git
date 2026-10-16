"""看看 Web 控制台要用的依赖装没装。"""

from __future__ import annotations

import importlib.util

WANT = [
    "fastapi",
    "uvicorn",
    "starlette",
    "jinja2",
    "sse_starlette",
    "typer",
    "rich",
    "httpx",
    "playwright",
    "openpyxl",
    "PIL",
    "pydantic",
]

for name in WANT:
    mod = importlib.util.find_spec(name)
    if mod is None:
        print(f"  ❌ {name}: 没装")
        continue
    try:
        import importlib.metadata as md

        ver = md.version(name if name != "PIL" else "pillow")
    except Exception:  # noqa: BLE001
        ver = "?"
    print(f"  ✅ {name}: {ver}")
