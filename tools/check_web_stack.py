"""确认 Web 控制台要用到的 API 真的可用（版本、StreamingResponse、StaticFiles）。"""

from __future__ import annotations

import importlib.metadata as md


def ver(name: str) -> str:
    try:
        return md.version(name)
    except Exception:  # noqa: BLE001
        return "?"


print(f"fastapi   : {ver('fastapi')}")
print(f"starlette : {ver('starlette')}")
print(f"uvicorn   : {ver('uvicorn')}")
print()

try:
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
    from fastapi.staticfiles import StaticFiles

    print("✅ fastapi 关键导入都可用")
except Exception as exc:  # noqa: BLE001
    print(f"❌ fastapi 导入失败：{exc}")

try:
    import starlette

    print(f"✅ starlette.StreamingResponse：{hasattr(starlette.responses, 'StreamingResponse')}")
    print(f"✅ starlette 版本：{starlette.__version__}")
except Exception as exc:  # noqa: BLE001
    print(f"❌ starlette：{exc}")

# 打一下 app 能不能建、路由能不能加
try:
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.get("/ping")
    def ping():
        return {"ok": True}

    with TestClient(app) as client:
        r = client.get("/ping")
    print(f"✅ TestClient 可用（/ping → {r.status_code} {r.json()}）")
except Exception as exc:  # noqa: BLE001
    print(f"❌ TestClient 不可用：{exc}")

try:
    import httpx

    print(f"✅ httpx {ver('httpx')}（TestClient 依赖它）")
except Exception as exc:  # noqa: BLE001
    print(f"❌ httpx：{exc}")
