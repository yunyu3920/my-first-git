"""把上传好的图片地址写进商品对象里。

**最容易踩的坑：这几个字段是 JSON 字符串，不是对象。**

    mainProductSkuSpecReqs  = '[{"previewImgUrls":"a.jpg|b.jpg", ...}]'
    variationListStr        = '[{"thumbUrl":"a.jpg", ...}]'

直接 `obj["previewImgUrls"] = ...` 会报错（字符串没有这个键）；而如果把它们当字符串
原样拼进保存载荷、又忘了同步改 JSON 里那一层，图片就换不掉。所以统一走这里，
把「可能是 str、也可能是 list」两种情况都处理掉。

要改的位置（对着真实商品 184807703165779045 逐个核过）：

    主图/轮播图  mainProductSkuSpecReqs[*].previewImgUrls   ← 竖线拼的多张
                 mainProductSkuSpecReqsList[*].previewImgUrls ← 同上（镜像字段）
    SKU 缩略图   variationListStr[*].thumbUrl
                 variations[*].thumbUrl                      ← 镜像字段
    素材图       materialImgUrl                             ← 顶层单个

## 轮播图怎么换（2026-10-16 用户定的规矩）

`previewImgUrls` 是个竖线拼的列表，**第 1 张是主图、后面几张是展示图**。换图的规矩是：

> 把模板的主图**删掉**，新的主图**顶到第 1 位**，模板的**其它展示图原样保留**。

    [模板主图, 展示1, 展示2, 展示3, 展示4]
        ↓
    [新主图, 展示1, 展示2, 展示3, 展示4]

**只换主图，不删展示图**。保留哪几张由调用方通过 `keep_previews` 传进来
（上架流程传的是**模板商品**的那几张，见 `listing.py`）；不传就退化成
"用这个商品自己现有的第 2 张往后"。

**颜色/规格一律不碰**——`parentSpecName`（颜色）、`specName`、`productSkuSpecReqs`
这些原样保留，只动图片地址。有测试钉住（`tests/test_swap.py`）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from temu_bot.product import as_list_field, set_list_field

log = logging.getLogger(__name__)


@dataclass
class ImagePatch:
    """换图的结果：`notes` 是真的改到手的地方，`warnings` 是想改没改成的。"""

    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        """至少有一处真改到了，才算换图成功。"""
        return bool(self.notes)

# 这些字段在接口里是"JSON 字符串"，改动时要先解析再序列化回去
JSON_STR_FIELDS = (
    "mainProductSkuSpecReqs",
    "mainProductSkuSpecReqsList",
    "variationListStr",
    "variations",
)


def _as_list(value: Any) -> tuple[list, bool]:
    """把字段变成 list。返回 (列表, 原本是不是字符串)。"""
    return as_list_field(value)


def _write_back(product: dict[str, Any], key: str, items: list, was_str: bool) -> None:
    set_list_field(product, key, items, was_str)


def preview_urls(product: dict[str, Any], key: str = "mainProductSkuSpecReqs") -> list[str]:
    """取出这条 SKC 的轮播图列表（第 1 张是主图，后面是展示图）。"""
    items, _ = _as_list(product.get(key))
    for entry in items:
        if isinstance(entry, dict) and entry.get("previewImgUrls"):
            return [u for u in str(entry["previewImgUrls"]).split("|") if u]
    return []


def showcase_images(product: dict[str, Any]) -> list[str]:
    """这个商品现有的**展示图**：轮播去掉第 1 张（主图）之后剩下的。"""
    return preview_urls(product)[1:]


def _set_preview_urls(product: dict[str, Any], key: str, joined: str) -> int:
    items, was_str = _as_list(product.get(key))
    if not items:
        return 0
    touched = 0
    for entry in items:
        if isinstance(entry, dict) and "previewImgUrls" in entry:
            entry["previewImgUrls"] = joined
            touched += 1
    if touched:
        _write_back(product, key, items, was_str)
    return touched


def _set_thumb_url(product: dict[str, Any], key: str, url: str) -> int:
    items, was_str = _as_list(product.get(key))
    if not items:
        return 0
    touched = 0
    for entry in items:
        if isinstance(entry, dict) and "thumbUrl" in entry:
            entry["thumbUrl"] = url
            touched += 1
    if touched:
        _write_back(product, key, items, was_str)
    return touched


def patch_product_images(
    product: dict[str, Any],
    *,
    main_urls: list[str],
    material_url: str | None = None,
    keep_previews: list[str] | None = None,
) -> ImagePatch:
    """把图片地址写进商品对象。

    `keep_previews` = 换完之后要**保留的展示图**（已经不含主图）。传 `None` 就
    用这个商品自己现有的第 2 张往后（`showcase_images`）。

    返回 `ImagePatch(notes, warnings)`：
      - `notes`  真的改到手的地方（"改了哪些"）——空 = 一张都没换上去，上层该中止；
      - `warnings` 想改但改不动的地方（比如商品压根没有 materialImgUrl 字段），
        不算失败，但必须让用户看见。

    只写**商品本来就有的字段**：接口对不带这个键的商品，多半会忽略我们塞进去的值
    （那图片就白传了，还得到一个"标题对、图是模板的"错商品）。所以字段不存在时
    宁可什么都不改，让上层按"没换成功"中止。

    **颜色/规格一概不碰**：只改 `previewImgUrls` 与 `thumbUrl` 两个键。
    """
    notes: list[str] = []
    warnings: list[str] = []
    main_urls = [u.strip() for u in main_urls if u and u.strip()]
    if not main_urls and not material_url:
        return ImagePatch(notes=notes, warnings=warnings)

    if main_urls:
        thumb = main_urls[0]
        if keep_previews is None:
            keep_previews = showcase_images(product)
            source = "这个商品原有的" if keep_previews else ""
        else:
            source = "模板的" if keep_previews else ""
        keep = [u.strip() for u in keep_previews if u and u.strip()]

        # 新主图顶到第 1 位，模板的展示图原样跟在后面
        joined = "|".join(main_urls + keep)

        for key in ("mainProductSkuSpecReqs", "mainProductSkuSpecReqsList"):
            n = _set_preview_urls(product, key, joined)
            if n:
                notes.append(
                    f"{key}: {n} 条轮播 → 新主图 {len(main_urls)} 张排第 1"
                    + (f"，保留{source}展示图 {len(keep)} 张" if keep else "，没有展示图要保留")
                )

        for key in ("variationListStr", "variations"):
            n = _set_thumb_url(product, key, thumb)
            if n:
                notes.append(f"{key}: {n} 条缩略图 → 新主图")

    if material_url:
        if "materialImgUrl" in product:
            product["materialImgUrl"] = material_url
            notes.append("materialImgUrl: 素材图")
        else:
            warnings.append("素材图没写进去：这个商品没有 materialImgUrl 字段")

    if not notes:
        log.warning("商品里没找到可写的图片字段——图片可能没换成功")
    return ImagePatch(notes=notes, warnings=warnings)


def describe_product_images(product: dict[str, Any]) -> dict[str, Any]:
    """给干跑用：现在商品里挂的是什么图。"""
    previews = preview_urls(product)

    var, _ = _as_list(product.get("variationListStr"))
    thumbs = {
        str(e.get("thumbUrl"))
        for e in var
        if isinstance(e, dict) and e.get("thumbUrl")
    }
    return {
        "preview_count": len(previews),
        "preview_first": previews[0] if previews else "",
        # 第 1 张是主图，其余是展示图（换图时保留的就是这几张）
        "showcase_count": max(0, len(previews) - 1),
        "thumb_count": len(thumbs),
        "material": product.get("materialImgUrl") or "",
    }
