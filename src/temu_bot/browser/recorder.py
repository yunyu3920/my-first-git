"""网络录制：在你手动操作一遍的同时，把店小秘的接口请求/响应原样记下来。

用途：拿到真实的请求体与响应结构，用它来写 API 客户端——比猜选择器靠谱得多。
（这也是原商业工具的做法：日志里全是 httpx 调 www.dianxiaomi.com/api/*.json。）

安全约定：
- cookie / authorization / token 类请求头的**值一律不落盘**，只记录"存在哪些头、cookie 有哪些名字"。
- 响应体默认截断（--max-body），避免把大响应整份写盘。
- 录制文件全部落在 D:\\temu_bot\\data\\reports\\recording\\（已在 .gitignore 里）。
"""

from __future__ import annotations

import base64
import io
import json
import logging
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from temu_bot.config import Config

log = logging.getLogger(__name__)

# 只录业务相关流量，静态资源/埋点不记
INTERESTING = re.compile(r"dianxiaomi\.com/(api|wxalbum)/|dianxiaomi\.com/api/", re.I)


# 录制哪些类型：静态资源（js/css/图片/字体）不录，其余业务流量都录。
BUSINESS_TYPES = {"document", "xhr", "fetch", "websocket"}


def _is_interesting(url: str, resource_type: str = "") -> bool:
    """业务请求 + **整页导航**都要记。

    只记 /api/ 会漏掉"点一下整页跳到编辑页"这种情况——那样点击看起来像没反应。
    """
    if INTERESTING.search(url):
        return True
    if "dianxiaomi.com" in url.lower() and (resource_type or "").lower() in BUSINESS_TYPES:
        return True
    return False

REDACT_HEADERS = (
    "cookie",
    "set-cookie",
    "authorization",
    "x-csrf",
    "csrf",
    "x-xsrf",
    "token",
    "x-auth",
    "sign",
    "secret",
)


# 在页面里挂钩 fetch/XHR，把请求体自己记下来。
# 为什么不用 Playwright 的 request.post_data？实测：保存商品那种 multipart 请求，
# post_data 和 post_data_buffer 都是空的（拿不到），只有页面侧才看得到真实内容。
REQUEST_HOOK_JS = r"""
(() => {
  if (window.__dxmHookInstalled) return 'already';
  window.__dxmHookInstalled = true;
  window.__dxmCaptured = [];
  window.__dxmBlobs = [];

  const LIMIT = 3000000;

  async function blobToBase64(blob) {
    const buffer = await blob.arrayBuffer();
    const bytes = new Uint8Array(buffer);
    let binary = '';
    const CHUNK = 0x8000;
    for (let i = 0; i < bytes.length; i += CHUNK) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
    }
    return btoa(binary);
  }

  async function bodyToText(url, body) {
    if (body == null) return null;
    if (typeof body === 'string') return body;
    if (body instanceof FormData) {
      const parts = [];
      for (const [k, v] of body.entries()) {
        if (typeof v === 'string') {
          parts.push(k + '=' + v);
        } else if (v instanceof Blob) {
          // 二进制必须按 base64 无损记（文本读法会把 zip 这类内容毁掉）
          let base64 = '';
          try { base64 = await blobToBase64(v); } catch (e) { base64 = ''; }
          parts.push('----- FILE FIELD: ' + k + '   filename=' + (v.name || '') +
                     '  size=' + v.size + '  encoding=base64 -----');
          try {
            window.__dxmBlobs.push({
              url: url, field: k, filename: v.name || '', size: v.size,
              type: v.type || '', base64: base64
            });
          } catch (e) {}
        } else {
          parts.push(k + '=<' + Object.prototype.toString.call(v) + '>');
        }
      }
      return parts.join('\n');
    }
    if (body instanceof URLSearchParams) return body.toString();
    try { return String(body); } catch (e) { return '<' + typeof body + '>'; }
  }

  function record(url, text) {
    try {
      if (typeof text === 'string' && text.length > LIMIT) {
        text = text.slice(0, LIMIT) + '…<truncated by hook>';
      }
      window.__dxmCaptured.push({ url: String(url || ''), body: text, at: new Date().toISOString() });
      if (window.__dxmCaptured.length > 300) window.__dxmCaptured.shift();
    } catch (e) {}
  }

  const origOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url) {
    this.__dxmUrl = url;
    return origOpen.apply(this, arguments);
  };
  const origSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function (body) {
    try {
      const url = this.__dxmUrl;
      Promise.resolve(bodyToText(url, body)).then((t) => { if (t) record(url, t); });
    } catch (e) {}
    return origSend.apply(this, arguments);
  };

  const origFetch = window.fetch;
  window.fetch = function (input, init) {
    try {
      const url = (typeof input === 'string') ? input : (input && input.url);
      const body = init && init.body;
      Promise.resolve(bodyToText(url, body)).then((t) => { if (t) record(url, t); });
    } catch (e) {}
    return origFetch.apply(this, arguments);
  };

  return 'installed';
})()
"""


class Recorder:
    """挂在一个 Playwright 上下文上，把匹配的请求/响应写成 JSONL。"""

    def __init__(self, cfg: Config, out_dir: Path | None = None, *, max_body: int = 20_000) -> None:
        self.cfg = cfg
        self.base_dir = out_dir or (cfg.data_dir / "reports" / "recording")
        self.max_body = max_body
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.jsonl_path = self.base_dir / f"{stamp}.jsonl"
        self.body_dir = self.base_dir / f"{stamp}-bodies"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.body_dir.mkdir(parents=True, exist_ok=True)

        self._seq = 0
        self._handle = self.jsonl_path.open("a", encoding="utf-8")
        self.request_count = 0
        self.endpoints: dict[str, int] = {}
        self.dialogs: list[str] = []
        self.console_errors: list[str] = []
        self.failed_requests: list[str] = []
        log.info("录制输出：%s", self.jsonl_path)

    # ------------------------------------------------------------ 写入
    def _write(self, payload: dict[str, Any]) -> None:
        self._handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._handle.flush()

    @staticmethod
    def _clean_headers(headers: dict[str, str]) -> tuple[dict[str, str], list[str]]:
        """返回 (可安全记录的请求头, cookie 名字列表)。"""
        safe: dict[str, str] = {}
        cookie_names: list[str] = []
        for name, value in (headers or {}).items():
            lowered = name.lower()
            if lowered == "cookie":
                cookie_names = [c.split("=")[0].strip() for c in value.split(";") if c.strip()]
                continue
            if any(key in lowered for key in REDACT_HEADERS):
                safe[name] = "<已隐藏>"
                continue
            safe[name] = value if len(value) < 500 else value[:500] + "…"
        return safe, cookie_names

    # ------------------------------------------------------------ 事件
    def attach(self, context: Any) -> "Recorder":
        context.on("request", self._on_request)
        context.on("response", self._on_response)
        # 页面侧挂钩：未来所有导航都自动注入
        try:
            context.add_init_script(REQUEST_HOOK_JS)
        except Exception as exc:  # noqa: BLE001
            log.debug("注入请求体钩子失败：%s", exc)
        # 弹窗 / 控制台 / 页面异常 / 失败请求——"点了没反应"的常见元凶都在这里
        for page in list(context.pages):
            self._hook_page(page)
        context.on("page", self._hook_page)
        return self

    def _hook_page(self, page: Any) -> None:
        try:
            page.on("dialog", self._on_dialog)
            page.on("console", self._on_console)
            page.on("pageerror", self._on_pageerror)
            page.on("requestfailed", self._on_request_failed)
        except Exception as exc:  # noqa: BLE001
            log.debug("挂页面监听失败：%s", exc)
        # 当前已加载的文档也要补一次（init script 只对之后的导航生效）
        try:
            page.evaluate(REQUEST_HOOK_JS)
        except Exception as exc:  # noqa: BLE001
            log.debug("当前页面注入钩子失败：%s", exc)

    def dump_page_captures(self, pages: list[Any]) -> dict[str, Any]:
        """把所有页面侧钩子记下来的请求体/文件汇总落盘。

        这是拿到 multipart 请求体（例如保存商品时那个 zip）的唯一可靠办法——
        Playwright 的 request.post_data / post_data_buffer 对这类请求都是空的。

        返回 {"text": 文本捕获的 json 路径, "files": [落盘的文件路径...]}。
        """
        collected: list[dict[str, Any]] = []
        blobs: list[dict[str, Any]] = []
        for page in pages:
            for expr, sink in (("window.__dxmCaptured || []", collected), ("window.__dxmBlobs || []", blobs)):
                try:
                    items = page.evaluate(expr)
                except Exception as exc:  # noqa: BLE001
                    log.debug("读取页面侧捕获失败(%s)：%s", expr, exc)
                    continue
                if isinstance(items, list):
                    sink.extend(items)

        result: dict[str, Any] = {"text": None, "files": []}

        if collected:
            path = self.body_dir / "page-request-bodies.json"
            path.write_text(json.dumps(collected, ensure_ascii=False, indent=2), encoding="utf-8")
            result["text"] = path
            log.info("页面侧请求体已落盘：%s（%d 条）", path, len(collected))

        # 二进制文件：base64 还原；是 zip 的话顺便把里面那份 JSON 解出来
        for index, blob in enumerate(blobs, start=1):
            try:
                raw = base64.b64decode(blob.get("base64") or "")
            except Exception as exc:  # noqa: BLE001
                log.warning("第 %d 个 blob 解码失败：%s", index, exc)
                continue
            if not raw:
                continue

            bin_path = self.body_dir / f"hook-blob-{index:02d}.bin"
            bin_path.write_bytes(raw)
            result["files"].append(bin_path)
            log.info("页面侧文件已落盘：%s（%d 字节）", bin_path, len(raw))

            if raw[:2] == b"PK":     # zip：把里面的条目解出来，通常是 choiceSave.txt
                try:
                    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                        for name in archive.namelist():
                            out = self.body_dir / f"hook-blob-{index:02d}-{Path(name).name}"
                            out.write_bytes(archive.read(name))
                            result["files"].append(out)
                            log.info("zip 内条目已解出：%s", out)
                except Exception as exc:  # noqa: BLE001
                    log.warning("zip 解包失败：%s", exc)

        return result

    def _on_dialog(self, dialog: Any) -> None:
        """Playwright 默认会静默取消弹窗 → 看起来就是"点击没反应"。

        这里记录它，并且**点确定**（贴近人工操作），否则流程会被卡死。
        """
        self.dialogs.append(f"[{dialog.type}] {dialog.message}")
        self._write(
            {
                "kind": "dialog",
                "seq": self._seq,
                "ts": datetime.now().isoformat(timespec="milliseconds"),
                "dialog_type": dialog.type,
                "message": (dialog.message or "")[:300],
                "action": "accept",
            }
        )
        log.warning("页面弹窗 [%s]：%s  → 已自动点确定", dialog.type, (dialog.message or "")[:200])
        try:
            dialog.accept()
        except Exception as exc:  # noqa: BLE001
            log.debug("弹窗 accept 失败：%s", exc)

    def _on_console(self, message: Any) -> None:
        try:
            level = message.type
        except Exception:  # noqa: BLE001
            return
        if level not in ("error", "warning"):
            return
        self.console_errors.append(f"[{level}] {message.text}")
        self._write(
            {
                "kind": "console",
                "ts": datetime.now().isoformat(timespec="milliseconds"),
                "level": level,
                "text": (message.text or "")[:500],
                "location": str(getattr(message, "location", ""))[:200],
            }
        )

    def _on_pageerror(self, error: Any) -> None:
        self.console_errors.append(f"[pageerror] {error}")
        self._write(
            {
                "kind": "pageerror",
                "ts": datetime.now().isoformat(timespec="milliseconds"),
                "message": str(error)[:500],
            }
        )

    def _on_request_failed(self, request: Any) -> None:
        failure = ""
        try:
            failure = str(request.failure or "")
        except Exception:  # noqa: BLE001
            pass
        self.failed_requests.append(f"{request.method} {request.url} → {failure}")
        self._write(
            {
                "kind": "requestfailed",
                "ts": datetime.now().isoformat(timespec="milliseconds"),
                "method": request.method,
                "url": request.url,
                "failure": failure[:300],
            }
        )

    def _on_request(self, request: Any) -> None:
        url = request.url
        if not _is_interesting(url, getattr(request, "resource_type", "")):
            return
        self._seq += 1
        self.request_count += 1
        endpoint = f"{request.method} {url.split('?')[0].split('dianxiaomi.com')[-1]}"
        self.endpoints[endpoint] = self.endpoints.get(endpoint, 0) + 1

        headers, cookie_names = self._clean_headers(request.headers)
        # multipart 这类请求 post_data 会返回 None，得退到 post_data_buffer（二进制）
        post_data = None
        try:
            post_data = request.post_data
        except Exception:  # noqa: BLE001
            post_data = None
        if not post_data:
            try:
                buffer = request.post_data_buffer
                if buffer:
                    post_data = bytes(buffer).decode("utf-8", "replace")
            except Exception as exc:  # noqa: BLE001
                log.debug("取 post_data_buffer 失败：%s", exc)

        # 大 body（例如保存商品那种把整个商品对象塞进文件里的 multipart）单独落盘，
        # 否则会被截断——而那种请求的 body 恰恰是最需要的东西。
        body_file = None
        if post_data is not None and len(post_data) > 4000:
            try:
                self.body_dir.mkdir(parents=True, exist_ok=True)
                body_file = self.body_dir / f"{self._seq:05d}-REQ-{request.method}.txt"
                body_file.write_text(post_data, encoding="utf-8")
            except Exception as exc:  # noqa: BLE001
                log.debug("请求体落盘失败：%s", exc)
                body_file = None

        if post_data and len(post_data) > self.max_body and not body_file:
            post_data = post_data[: self.max_body] + f"…<截断，原长 {len(post_data)}>"

        self._write(
            {
                "kind": "request",
                "seq": self._seq,
                "ts": datetime.now().isoformat(timespec="milliseconds"),
                "method": request.method,
                "url": url,
                "resource_type": request.resource_type,
                "headers_names": sorted(headers),
                "headers": headers,
                "cookie_names": cookie_names,
                "body_len": len(post_data) if post_data else 0,
                "body_file": str(body_file) if body_file else None,
                "post_data": None if body_file else post_data,
            }
        )

    def _on_response(self, response: Any) -> None:
        url = response.url
        if not _is_interesting(url, getattr(response.request, "resource_type", "")):
            return
        request = response.request
        entry: dict[str, Any] = {
            "kind": "response",
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "method": request.method,
            "url": url,
            "status": response.status,
            "content_type": (response.headers or {}).get("content-type", ""),
        }

        # 只给 JSON 文本响应存 body（图片/二进制不存）
        ctype = entry["content_type"] or ""
        if any(k in ctype for k in ("json", "text", "javascript", "html")):
            try:
                body = response.text()
                truncated = len(body) > self.max_body
                if truncated:
                    body = body[: self.max_body]
                self._seq += 1
                body_file = self.body_dir / f"{self._seq:05d}-{request.method}.txt"
                body_file.write_text(body, encoding="utf-8")
                entry["body_file"] = str(body_file)
                entry["body_truncated"] = truncated
                entry["body_len"] = len(body)
            except Exception as exc:  # noqa: BLE001 - body 取不到（重定向/大流）很常见
                entry["body_error"] = str(exc)[:200]

        self._write(entry)

    # ------------------------------------------------------------ 收尾
    def summary(self) -> dict[str, Any]:
        return {
            "requests": self.request_count,
            "endpoints": dict(sorted(self.endpoints.items(), key=lambda kv: -kv[1])),
            "dialogs": self.dialogs,
            "console_errors": self.console_errors[:20],
            "failed_requests": self.failed_requests[:20],
            "jsonl": str(self.jsonl_path),
            "bodies": str(self.body_dir),
        }

    def close(self) -> None:
        try:
            self._handle.close()
        except Exception:  # noqa: BLE001
            pass
