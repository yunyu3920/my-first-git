r"""从原商业工具的日志里还原它到底调了哪些接口、按什么顺序。

它用 urllib3 的 DEBUG 级日志，每行形如：
  2026-10-12 09:12:33,123 - urllib3.connectionpool - DEBUG - http://host:80 "POST /path HTTP/1.1" 200 339
所以能直接抽出 (host, method, path, status) 的时序，用来反推真实业务流程。

用法: python tools\mine_old_logs.py <日志文件...> [--grep 关键词]
"""

from __future__ import annotations

import collections
import re
import sys

# urllib3 老格式：http://host:80 "POST /path HTTP/1.1" 200 339
URLLIB3 = re.compile(
    r'(?P<url>https?://[^\s"]+)"?\s*"?(?P<method>GET|POST|PUT|DELETE|PATCH)\s+(?P<path>\S+)\s+'
    r'HTTP/[\d.]+"?\s+(?P<status>\d{3})'
)
# httpx 新格式：HTTP Request: POST https://host/path "HTTP/1.1 200 "
HTTPX = re.compile(
    r'HTTP Request: (?P<method>[A-Z]+) (?P<url>https?://\S+?) "HTTP/[\d.]+ (?P<status>\d{3})'
)


def extract(line: str):
    """从一行日志里抽出 (host, method, path, status)；不是 HTTP 行就返回 None。"""
    m = HTTPX.search(line)
    if m:
        url = m.group("url")
        host = re.sub(r"^https?://", "", url).split(":")[0].split("/")[0]
        path = url[len(re.match(r"https?://[^/]+", url).group(0)):] or "/"
        return host, m.group("method"), path, m.group("status")

    m = URLLIB3.search(line)
    if m:
        url = m.group("url")
        host = re.sub(r"^https?://", "", url).split(":")[0].split("/")[0]
        return host, m.group("method"), m.group("path"), m.group("status")
    return None


def parse(paths: list[str], grep: str | None, limit: int, *, flow: bool = False) -> None:
    hosts: collections.Counter[str] = collections.Counter()
    endpoints: collections.Counter[str] = collections.Counter()
    sequence: list[tuple[str, str, str]] = []
    first_seen: dict[str, int] = {}

    for path in paths:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if grep and grep not in line:
                    continue
                got = extract(line)
                if not got:
                    continue
                host, method, ep, status = got
                if "NetVer/webapi" in ep:          # 授权心跳，与业务流程无关
                    continue
                key = f"{method} {host}{ep[:120]}"
                hosts[host] += 1
                endpoints[key] += 1
                first_seen.setdefault(key, len(sequence))
                sequence.append((method, f"{host}{ep[:160]}", status))

    print("=== 出现过的 host 统计 ===")
    for host, n in hosts.most_common(40):
        print(f"{n:>7} {host}")

    if flow:
        print("\n=== 首次出现顺序（业务流程还原）===")
        for key, idx in sorted(first_seen.items(), key=lambda kv: kv[1]):
            print(f"{idx:>5}  {key}   ×{endpoints[key]}")
        return

    print(f"\n=== 端点统计（前 {limit}）===")
    for ep, n in endpoints.most_common(limit):
        print(f"{n:>7} {ep}")

    print(f"\n=== 时序（前 {limit} 条）===")
    for method, ep, status in sequence[:limit]:
        print(f"{method:<4} {status} {ep}")


def main() -> None:
    argv = sys.argv[1:]
    grep: str | None = None
    limit = 60
    flow = False
    paths: list[str] = []

    i = 0
    while i < len(argv):
        token = argv[i]
        if token == "--grep":
            grep = argv[i + 1]
            i += 2
        elif token == "--limit":
            limit = int(argv[i + 1])
            i += 2
        elif token == "--flow":
            flow = True
            i += 1
        else:
            paths.append(token)
            i += 1

    if not paths:
        print(__doc__)
        return
    parse(paths, grep, limit, flow=flow)


if __name__ == "__main__":
    main()
