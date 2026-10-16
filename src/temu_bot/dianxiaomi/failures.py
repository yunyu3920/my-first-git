"""发布失败分类：把店小秘返回的 errMsg 归成可处置的几类。

实测样本（2026-10-15，待发布 436 条里 408 条发布失败）：
    203  Temu对不同店铺有每天发品的数量限制…请联系Temu买手/运营询问是否可提升上限
    195  接口报错:英文名称不可输入非英文字母或表情符号
      4  接口报错:创建商品数量超过当日额度，请于次日创建。
      3  接口报错:系统异常
      3  接口报错:Chinese or English title contains non-compliant words

关键区别：**哪类能靠工具自动救回来，哪类只能等/提额**。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable


class Fix(str, Enum):
    """处置方式。"""

    AUTO_FIX_TITLE = "自动修标题"      # 工具能改好再发（清洗/换词）
    AUTO_FIX_IMAGE = "自动修图"        # 图片规格不达标，修成合规尺寸再发
    AUTO_RETRY = "自动重试"            # 临时故障，限速重发即可
    WAIT_QUOTA = "等额度"              # 平台每日额度限制，只能分批/次日或找买手提额
    REAUTH = "需重新授权"              # 店铺在 Temu 的授权过期，只能你去重新授权
    MANUAL = "需人工"                  # 看不懂的，留给人


@dataclass(frozen=True, slots=True)
class Category:
    code: str
    label: str
    fix: Fix
    advice: str
    keywords: tuple[str, ...] = ()


CATEGORIES: tuple[Category, ...] = (
    # 排最前：授权过期会拦下**整个店**的发品，优先级高于其它一切
    Category(
        code="AUTH_EXPIRED",
        label="店铺授权过期（Temu 侧）",
        fix=Fix.REAUTH,
        advice="Temu 给店小秘的 access_token 过期了，整店都发不出去。到店小秘"
               "「店铺授权」里对这家店重新授权即可，工具无法也不应绕过。",
        keywords=("access_token is expired", "reauthorize", "授权过期", "重新授权", "token失效"),
    ),
    Category(
        code="DAILY_QUOTA",
        label="平台每日发品额度已满",
        fix=Fix.WAIT_QUOTA,
        advice="Temu 对每个店铺有每日发品上限。分批发/次日再发，或联系 Temu 买手提额。",
        keywords=("每天发品的数量限制", "超过当日额度", "每日发品", "发品数量限制"),
    ),
    Category(
        code="IMAGE_SPEC",
        label="图片规格不达标",
        fix=Fix.AUTO_FIX_IMAGE,
        advice="主图/轮播图要 3:4、宽>=1340px、高>=1785px、<=2MB。按规格重出图后重发。",
        keywords=("skc轮播图", "校验失败，应符合宽高比例", "宽高比例为3:4", "图片尺寸", "主图校验"),
    ),
    Category(
        code="EN_TITLE_BAD_CHARS",
        label="英文标题含非英文字符",
        fix=Fix.AUTO_FIX_TITLE,
        advice="英文标题里混了中文/表情/特殊符号。清洗或重写英文标题后重新发布即可。",
        keywords=("英文名称不可输入非英文字母", "表情符号"),
    ),
    Category(
        code="TITLE_BANNED_WORDS",
        label="标题含违规词",
        fix=Fix.AUTO_FIX_TITLE,
        advice="标题命中平台违禁词。换词后重发；可用 /api/bannedWord 接口先自查。",
        keywords=("non-compliant words", "违禁", "违规词"),
    ),
    Category(
        code="TRANSIENT",
        label="临时系统异常",
        fix=Fix.AUTO_RETRY,
        advice="接口偶发异常，限速重试即可。",
        keywords=("系统异常", "请稍后重试", "超时", "timeout"),
    ),
)

UNKNOWN = Category(
    code="UNKNOWN",
    label="未识别的原因",
    fix=Fix.MANUAL,
    advice="先看原始 errMsg，必要时补进分类表。",
)


@dataclass(slots=True)
class Failure:
    product_id: str
    title: str
    err_msg: str
    category: Category
    state: str = ""


@dataclass(slots=True)
class Triage:
    failures: list[Failure] = field(default_factory=list)

    def by_category(self) -> dict[str, list[Failure]]:
        out: dict[str, list[Failure]] = {}
        for item in self.failures:
            out.setdefault(item.category.code, []).append(item)
        return out

    def counts(self) -> dict[str, int]:
        return {code: len(items) for code, items in self.by_category().items()}

    def counts_by_fix(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for item in self.failures:
            key = item.category.fix.value
            out[key] = out.get(key, 0) + 1
        return out


def classify(err_msg: str | None) -> Category:
    """按关键字把 errMsg 归到某一类（先匹配到的优先）。"""
    text = (err_msg or "").strip()
    if not text:
        return UNKNOWN
    for category in CATEGORIES:
        if any(keyword in text for keyword in category.keywords):
            return category
    return UNKNOWN


def build_triage(rows: Iterable[dict]) -> Triage:
    """把店小秘列表行转成失败清单（只留发布失败的）。"""
    triage = Triage()
    for row in rows:
        err = str(row.get("errMsg") or "").strip()
        state = str(row.get("dxmOfflineState") or "")
        if not err and state != "publishFail":
            continue
        triage.failures.append(
            Failure(
                product_id=str(row.get("id") or ""),
                title=str(row.get("productName") or ""),
                err_msg=err,
                category=classify(err),
                state=state,
            )
        )
    return triage
