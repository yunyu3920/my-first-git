"""列出店小秘新版"通用命名空间" /api/product/<平台>/<动作>.json 下的所有动作。

新版前端很多平台共用一个命名空间，平台名是变量（如 pe="emag"）。
字面量搜索找不到，必须把模板字符串抽出来看。
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

JS_DIR = Path(r"D:\temu_bot\data\reports\js_all")
# 形如：/api/product/${xxx}/yyy.json   或   `/api/product/${pe}/batchPublish.json`
TEMPLATE = re.compile(r"/api/product/\$\{([A-Za-z0-9_]+)\}/([A-Za-z0-9_\-\.]+\.json)")
# 平台变量赋值，如  pe="emag";   或者  const pe = "popTemu"
PLATFORM_VAR = re.compile(r"([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*[\"']([A-Za-z0-9_]+)[\"']")


def main() -> None:
    files = sorted(JS_DIR.glob("*.js"))
    actions: Counter[str] = Counter()
    occurrences: list[tuple[str, str, str]] = []

    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        if "/api/product/${" not in text:
            continue
        # 该文件里平台变量 → 平台名
        var_to_platform = {m.group(1): m.group(2) for m in PLATFORM_VAR.finditer(text)}
        for match in TEMPLATE.finditer(text):
            var, action = match.group(1), match.group(2)
            platform = var_to_platform.get(var, f"<{var}>")
            actions[action] += 1
            occurrences.append((path.name, platform, action))

    print("=== 通用命名空间下的动作（动作 → 次数）===")
    for action, count in actions.most_common():
        print(f"  {count:>3}  {action}")

    print("\n=== 按平台聚合 ===")
    by_platform: dict[str, set[str]] = {}
    for _, platform, action in occurrences:
        by_platform.setdefault(platform, set()).add(action)
    for platform, acts in sorted(by_platform.items()):
        print(f"  {platform}: {', '.join(sorted(acts))}")


if __name__ == "__main__":
    main()
