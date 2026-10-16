"""商品对象的小工具：英文标题归一化、标题读写。

起因（实测）：195 条"英文标题不可输入非英文字母"的失败，根因不是"标题写成中文了"，
而是**隐形异体字符**——例如排版连字符 U+2010「‐」冒充 ASCII 的「-」：

    Women Long Sleeve T‐Shirt, Christmas Creative Drawing Print, ...
                        ^ U+2010，肉眼与 '-' 无异

典型来源：AI 生成标题、从 Word/网页复制、输入法全角。修法是**字符归一化**，
不需要翻译模型。
"""

from __future__ import annotations

import json
import re
from typing import Any

# 形近字符 → ASCII（关键是这些"看起来一样但不是 ASCII"的家伙）
PUNCT_MAP = str.maketrans(
    {
        # 各种连字符/破折号 → -
        "‐": "-",  # HYPHEN
        "‑": "-",  # NON-BREAKING HYPHEN
        "‒": "-",  # FIGURE DASH
        "–": "-",  # EN DASH
        "—": "-",  # EM DASH
        "―": "-",  # HORIZONTAL BAR
        "−": "-",  # MINUS SIGN
        "－": "-",  # 全角连字符
        # 引号 → ' 或 "
        "‘": "'",
        "’": "'",
        "‚": "'",
        "‛": "'",
        "′": "'",
        "“": '"',
        "”": '"',
        "„": '"',
        "″": '"',
        # 空白 → 普通空格
        " ": " ",
        " ": " ",
        " ": " ",
        "　": " ",
        " ": " ",
        "​": "",  # 零宽空格，直接删
        # 省略号/全角标点 → ASCII
        "…": "...",
        "，": ",",
        "、": ",",
        "：": ":",
        "；": ";",
        "（": "(",
        "）": ")",
        "！": "!",
        "？": "?",
        "。": ".",
        "．": ".",
        "％": "%",
        "＋": "+",
        "＆": "&",
    }
)

# 允许保留的 ASCII 字符；其余（汉字、表情、杂符号）替换成空格
ALLOWED_RE = re.compile(r"[^A-Za-z0-9 \-'\"&.,/()#%+!:;?@*]")
NON_ASCII_RE = re.compile(r"[^\x00-\x7f]")
SPACE_RE = re.compile(r"\s+")


def normalize_en_title(text: str) -> str:
    """把英文标题归一化成纯 ASCII 英文。

    1. 形近的排版字符换成 ASCII（这一步最关键）
    2. 剩下非 ASCII 的（汉字/表情）换成空格
    3. 压缩空白并去掉首尾的杂符号
    """
    if not text:
        return ""
    normalized = text.translate(PUNCT_MAP)
    normalized = ALLOWED_RE.sub(" ", normalized)
    normalized = SPACE_RE.sub(" ", normalized).strip()
    return normalized.strip(" -.,;:")


def has_non_ascii(text: str) -> bool:
    """是否含非 ASCII 字符（店小秘那个校验拦的就是这个）。"""
    return bool(NON_ASCII_RE.search(text or ""))


def bad_chars(text: str) -> list[str]:
    """列出所有非 ASCII 字符（去重，用于报告）。"""
    return sorted(set(NON_ASCII_RE.findall(text or "")))


# ------------------------------------------------------------------ 商品对象
def get_en_title(product: dict[str, Any]) -> str:
    """从 productNameI18n（形如 {"en":"..."}）里取英文标题。"""
    raw = product.get("productNameI18n")
    if not raw:
        return ""
    if isinstance(raw, dict):
        return str(raw.get("en") or "")
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return str(parsed.get("en") or "")
    except (ValueError, TypeError):
        pass
    return ""


def set_en_title(product: dict[str, Any], text: str) -> None:
    """写回英文标题（保持 productNameI18n 的 JSON 字符串形态，前端就是这么存的）。"""
    raw = product.get("productNameI18n")
    mapping: dict[str, Any] = {}
    if isinstance(raw, dict):
        mapping = dict(raw)
    elif raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                mapping = parsed
        except (ValueError, TypeError):
            mapping = {}
    mapping["en"] = text
    product["productNameI18n"] = json.dumps(mapping, ensure_ascii=False)


def fix_en_title(product: dict[str, Any]) -> tuple[str, str]:
    """归一化英文标题并写回。返回 (原值, 新值)。"""
    original = get_en_title(product)
    fixed = normalize_en_title(original)
    if fixed != original:
        set_en_title(product, fixed)
    return original, fixed


# ------------------------------------------------ "可能是 JSON 字符串"的列表字段
def as_list_field(value: Any) -> tuple[list, bool]:
    """把商品字段变成 list。返回 (列表, 原本是不是字符串)。

    `variationListStr` 在接口里是 **JSON 字符串**；`variations` 是真列表。
    同一个东西两种形态，所以读写都要兜住——见 `set_list_field`。
    """
    if isinstance(value, list):
        return value, False
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return [], True
        return (parsed if isinstance(parsed, list) else []), True
    return [], isinstance(value, str)


def set_list_field(product: dict[str, Any], key: str, items: list, was_str: bool) -> None:
    """按原来的形态写回去（原来是 JSON 字符串就仍然序列化成字符串）。"""
    if was_str:
        product[key] = json.dumps(items, ensure_ascii=False)
    else:
        product[key] = items


# ------------------------------------------------------------------ SKU 货号
# 实测（对着老工具上架成功的 98 个商品核过）：
#   SKU 级  variationListStr[*].extCode = 表里第 9 列那个前缀编码
#   SKC 级  mainProductSkuSpecReqs[*].extCode = **模板自己的**（老工具没动）
# 所以这里只改 SKU 级，SKC 级原样保留。
SKU_LIST_FIELDS = ("variationListStr", "variations")


def sku_codes(product: dict[str, Any]) -> list[str]:
    """商品上现有的 SKU 货号（去重、保持出现顺序）。"""
    seen: list[str] = []
    for key in SKU_LIST_FIELDS:
        items, _ = as_list_field(product.get(key))
        for entry in items:
            if isinstance(entry, dict):
                code = str(entry.get("extCode") or "")
                if code and code not in seen:
                    seen.append(code)
    return seen


def set_sku_code(product: dict[str, Any], *, sku_code: str) -> tuple[int, str]:
    """把每个 SKU 的 `extCode` 换成表里那一行生成的 SKU 货号。

    返回 `(改了几条, 原来的货号)`。**原来的货号可能有好几种**，这时返回的是用
    `、` 拼起来的（说明商品状态不干净，值得看一眼）。

    只动 **SKU 级**。SKC 级的 `extCode`（`mainProductSkuSpecReqs[*]`）原样保留。
    """
    before = "、".join(sku_codes(product))
    if not sku_code:
        return 0, before

    changed = 0
    for key in SKU_LIST_FIELDS:
        items, was_str = as_list_field(product.get(key))
        if not items:
            continue
        touched = 0
        for entry in items:
            if isinstance(entry, dict):
                if entry.get("extCode") != sku_code:
                    touched += 1
                entry["extCode"] = sku_code
        if touched:
            set_list_field(product, key, items, was_str)
            changed = max(changed, touched)
    return (changed if sku_code != before else 0), before
