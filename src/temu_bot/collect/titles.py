"""读「图片文件名清单」，按 11 位 ID 取中文 / 英文标题。

清单是「图片批处理生成清单」导出的那张表，结构是：

    A 列 = 数字前缀（11 位 ID，**文本**，可能带前导 0）
    B 列 = 中文后缀（= 中文标题）
    C 列以后 = 有的清单把英文标题放在后面（用户的实际文件放在 J 列）

用户原来是用 Excel 公式做的：

    =VLOOKUP(I2, 图片文件名清单.xlsx!$A:$B, 2, 0)

这里在程序里做，顺手绕开 VLOOKUP 的几个坑：

- **数字 vs 文本**：`00128479653` 和 `128479653` 在 VLOOKUP 里对不上，
  这里一律按**文本**归一后精确比对（而且**绝不转 int**，前导 0 不会丢）；
- **英文标题不在固定列**：自动在 C 列之后找第一格"像英文的"（有字母、没有汉字）；
- 找不到的 ID 不会报错，只是留空，并在预览里列出来。
"""

from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

log = logging.getLogger(__name__)

# 重命名工具允许 4–32 位，但实际用 11 位。全是数字才算 ID，表头/备注自动跳过。
_ID_RE = re.compile(r"^\d{4,32}$")
_CJK_RE = re.compile(r"[㐀-䶿一-鿿]")
_LATIN_RE = re.compile(r"[A-Za-z]")

# 英文列如果有个正经表头，别把它当标题（否则整列会错一行）
_HEADER_WORDS = {"english", "english title", "en", "en title", "title", "title (en)"}


def normalize_key(value: Any) -> str:
    """把单元格里的 ID 归一成文本。**不能转 int**——前导 0 会没。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text


def looks_english(text: str) -> bool:
    """像英文标题：有拉丁字母、没有汉字。"""
    return bool(_LATIN_RE.search(text)) and not _CJK_RE.search(text)


def _looks_like_header(text: str) -> bool:
    return text.strip().lower().rstrip(":：") in _HEADER_WORDS


@dataclass(slots=True)
class TitleEntry:
    key: str
    title_cn: str = ""
    title_en: str = ""


@dataclass(slots=True)
class TitleList:
    path: Path
    entries: dict[str, TitleEntry] = field(default_factory=dict)
    rows: int = 0
    with_en: int = 0
    # 英文列比 ID 列多一行（第一行就写着英文标题）→ 已自动往下对齐。
    # 用户实际那份清单就是这样，不处理的话每个商品的英文都会错到下一个商品上。
    shifted: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.entries)

    def lookup(self, key: str) -> TitleEntry | None:
        return self.entries.get(normalize_key(key))


def _iter_xlsx(path: Path) -> Iterator[tuple[Any, ...]]:
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        yield from wb.worksheets[0].iter_rows(values_only=True)
    finally:
        wb.close()


def _iter_csv(path: Path) -> Iterator[tuple[str, ...]]:
    """导出的 CSV 是 GBK；也兼容 UTF-8（带不带 BOM 都行）。"""
    raw = path.read_bytes()
    text = ""
    for enc in ("utf-8-sig", "gbk", "utf-16"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
    yield from csv.reader(text.splitlines())


def _rows_of(path: Path) -> Iterator[tuple[Any, ...]]:
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        return _iter_xlsx(path)
    if suffix == ".csv":
        return _iter_csv(path)
    if suffix == ".xls":
        raise ValueError("不支持 .xls（老格式），请用 .xlsx 或 .csv")
    raise ValueError(f"不认识的清单格式：{suffix or '(没有扩展名)'}")


def load_title_list(path: Path | str) -> TitleList:
    """读清单。读不了不抛异常，把原因放进 `error`（预览里要显示给人看）。"""
    path = Path(path)
    result = TitleList(path=path)

    if not path.is_file():
        result.error = f"找不到图片文件名清单：{path}"
        return result

    try:
        raw = [tuple(r) for r in _rows_of(path)]

        # 1) 找出数据行：A 列是纯数字 ID
        data: list[tuple[str, str, int]] = []      # (ID, 中文, 行下标)
        for i, row in enumerate(raw):
            if not row:
                continue
            key = normalize_key(row[0])
            if not _ID_RE.match(key):
                continue  # 表头 / 空行 / 备注行
            title_cn = str(row[1]).strip() if len(row) > 1 and row[1] is not None else ""
            data.append((key, title_cn, i))

        # 2) 每一行「C 列之后第一格像英文的」
        def _english_of(row: tuple[Any, ...]) -> str:
            for cell in row[2:]:
                text = str(cell).strip() if cell is not None else ""
                if text and looks_english(text) and not _looks_like_header(text):
                    return text
            return ""

        english_by_row = [_english_of(row) for row in raw]

        # 3) 处理"英文列比 ID 列多一行"的实际情况（用户那份清单就是）。
        #    A1 是表头「数字前缀」，J1 却已经是一条英文标题 → 英文整列往上错了一行。
        #    判断依据：第一条 ID 之前的行里出现了英文，而且英文总数正好等于数据行数。
        first_data = data[0][2] if data else 0
        leading = [t for t in english_by_row[:first_data] if t]
        english_values = [t for t in english_by_row if t]
        sequential = bool(leading) and len(english_values) == len(data)

        for pos, (key, title_cn, row_idx) in enumerate(data):
            if key in result.entries:
                continue  # 同一个 ID 出现多次：以第一条为准
            title_en = english_values[pos] if sequential else english_by_row[row_idx]
            result.entries[key] = TitleEntry(key=key, title_cn=title_cn, title_en=title_en)
            result.rows += 1
            if title_en:
                result.with_en += 1

        result.shifted = sequential
    except Exception as exc:  # noqa: BLE001 - 读清单失败不该拖垮收集
        result.error = f"读清单失败：{exc}"
        log.warning("读图片文件名清单失败（%s）：%s", path, exc)
        return result

    if not result.entries:
        result.error = "清单里没读到任何「ID + 标题」的行（A 列得是纯数字 ID）"
    return result
