"""店小秘接口客户端（httpx）。

端点来自对原工具日志的还原（4 天日志统计）：
    GET  /api/userIn.json / /api/userInfo.json          账号/店铺信息
    POST /api/popTemuProduct/pageList.json              半托管商品列表（分页）
    GET  /api/popTemuProduct/edit.json?id=<商品ID>       商品详情
    POST /api/banjia/copyProduct.json                   复制商品
    GET  /api/album/index.json                          图片空间（余量/列表）
    POST /api/cos/getSign.json                          取对象存储上传签名
    PUT  https://wxalbum-*.dianxiaomi.com/wxalbum/...   直传图片（不走本客户端 base_url）
    POST /api/cos/cosDxmCallBack.json                   上传回调登记
    POST /api/popTemuProduct/add<hash>.json             保存商品

**注意**：POST 类的具体请求体字段还没确认，等 `temu-bot record` 录到真实请求后填。
本文件先把传输层、登录态、错误处理、健康检查做扎实——这些与载荷无关。
"""

from __future__ import annotations

import io
import json
import logging
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from temu_bot.config import Config
from temu_bot.dianxiaomi.session import ShopSession, load_session, session_path

log = logging.getLogger(__name__)

BASE_URL = "https://www.dianxiaomi.com"

# 图片上传用的对象存储参数（从店小秘前端 JS 里挖出来的固定值）
COS_BUCKET = "wxalbum"
COS_REGION = "ap-shanghai"
UPLOAD_TIMEOUT = 180.0


def _guess_image_mime(suffix: str) -> str:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
        ".gif": "image/gif",
    }.get((suffix or "").lower(), "image/jpeg")


class DxmError(RuntimeError):
    """店小秘接口调用失败。"""


class NotLoggedIn(DxmError):
    """没有可用登录态（没登录过 / 已过期）。"""


class ShopMismatch(DxmError):
    """当前登录的店小秘账号里没有目标店铺——绝不能继续，否则会铺到错店。"""


"""店小秘账号里的「店」不全是真店铺。

`shopMap` 里除了真正的平台店铺，还会带几个**内部占位桶**（实测见到的有
「手工订单」，`id` 是 0）。它们不是能上架商品的店，报给用户看只会添乱，
所以 `verify_shop` 的"账号里有哪些店"会把这几个滤掉。
`shops()` 本身不过滤——那是接口返回的原样数据，别在上面动手脚。
"""
PLACEHOLDER_SHOPS = frozenset({"手工订单"})


def is_real_shop(shop: dict[str, Any]) -> bool:
    """这条 shopMap 记录是不是**真店铺**（滤掉占位桶）。"""
    name = str(shop.get("name") or "").strip()
    if not name or name in PLACEHOLDER_SHOPS:
        return False
    return str(shop.get("id") or "0") not in ("", "0")


def normalize_shop_name(name: Any) -> str:
    """店铺名归一化：去空格、转小写。比较店铺名一律走这里。"""
    return _norm(name)


def _is_real_shop(shop: dict[str, Any]) -> bool:
    return is_real_shop(shop)


@dataclass(slots=True)
class ShopLookup:
    """按店铺名找登录身份的结果。"""

    shop: str
    login: str = ""              # 找到了就是哪个身份
    shop_id: str = ""
    matched_name: str = ""
    scanned: list[dict[str, str]] = field(default_factory=list)   # [{login, reason}]
    available: list[str] = field(default_factory=list)            # 所有身份账号里见过的店名

    @property
    def ok(self) -> bool:
        return bool(self.login)

    def message(self) -> str:
        if self.ok:
            return (
                f"表里「{self.shop}」→ 登录身份「{self.login}」的账号里有"
                f"「{self.matched_name}」(shopId={self.shop_id})"
            )
        bits = "；".join(f"{s['login']}（{s['reason']}）" for s in self.scanned) or "（没有可用的登录身份）"
        return (
            f"表里要上「{self.shop}」，但**没有任何登录身份的账号里有这家店**。"
            f"查过的身份：{bits}。"
            + (f"这些账号里见到过的店：{'、'.join(self.available)}。" if self.available else "")
        )


# 解出来的结果缓存几秒：一批几十行、店铺名往往一样，不必每行都去问一遍接口。
# 缓存时间很短，用户中途登录/切店后重新跑就能立刻生效。
_LOOKUP_TTL_SECONDS = 20.0
_LOOKUP_CACHE: dict[tuple[str, str], tuple[float, ShopLookup]] = {}


def clear_lookup_cache() -> None:
    _LOOKUP_CACHE.clear()


def find_login_for_shop(
    cfg: Any,
    shop_name: str,
    *,
    candidates: list[str] | None = None,
    client_factory: Any = None,
) -> ShopLookup:
    """**表里这家店，是哪个登录身份的账号？**

    用户 2026-10-16：不要再让人手选登录身份，**程序根据表格里的店铺名自己认**。
    做法：拿表里的店铺名，去挨个问"这个身份的账号里有没有这家店"（实时读 `userIn`），
    谁有就是谁。

    `candidates` 不给就按 `cfg.logins()` 来（**有会话文件的排前面**——
    没登录的身份连问都问不了）。
    """
    shop_name = (shop_name or "").strip()
    if not shop_name:
        return ShopLookup(shop=shop_name, scanned=[{"login": "-", "reason": "表里没填店铺名"}])

    key = (str(cfg.root), shop_name)
    cached = _LOOKUP_CACHE.get(key)
    if cached is not None and time.monotonic() - cached[0] < _LOOKUP_TTL_SECONDS:
        return cached[1]

    if candidates is None:
        candidates = [i.login for i in cfg.logins() if i.has_session]
        if not candidates:
            candidates = [i.login for i in cfg.logins()]

    result = ShopLookup(shop=shop_name)
    seen_shops: list[str] = []
    for login in candidates:
        try:
            if client_factory is not None:
                client = client_factory(login)
            else:
                client = DxmClient(cfg, login)
            with client:
                shops = client.shops()
                check = client.verify_shop(shop_name)
        except NotLoggedIn:
            result.scanned.append({"login": login, "reason": "没登录 / 登录态过期"})
            continue
        except DxmError as exc:
            result.scanned.append({"login": login, "reason": f"接口不可用：{str(exc)[:60]}"})
            continue
        except Exception as exc:  # noqa: BLE001 - 一个身份问不动，不该拖垮整轮
            result.scanned.append({"login": login, "reason": f"出错：{str(exc)[:60]}"})
            continue

        for s in shops:
            name = str(s.get("name") or "")
            if name and name not in seen_shops:
                seen_shops.append(name)
        if check.ok:
            result.login = login
            result.shop_id = str(check.matched_id)
            result.matched_name = check.matched_name
            break
        result.scanned.append({"login": login, "reason": "账号里没有这家店"})

    result.available = seen_shops
    _LOOKUP_CACHE[key] = (time.monotonic(), result)
    return result


def _norm(name: Any) -> str:
    return " ".join(str(name or "").split()).lower()


@dataclass(slots=True)
class ShopCheck:
    """店铺一致性核对结果。"""

    expected: list[str]
    ok: bool
    matched_name: str | None = None
    matched_id: str | None = None
    available: list[str] = field(default_factory=list)

    def message(self) -> str:
        exp = "、".join(self.expected)
        if self.ok:
            return f"店铺一致：{self.matched_name}（shopId={self.matched_id}）"
        avail = "、".join(self.available) or "（无）"
        return (
            f"店铺不一致：表里要上的是「{exp}」，但当前登录的店小秘账号里只有：{avail}。"
            f"请先切换到对应的店铺/账号再继续。"
        )


# 保存接口的字段表与类型 —— 从抓到的真实载荷（choiceSave.txt）推导。
# 注意：多数 id 类字段在商品对象里是 int，提交时必须转成字符串。
SAVE_FIELD_TYPES: dict[str, type] = {
    "attributes": str,
    "categoryId": str,
    "categoryType": int,
    "description": str,
    "dxmDetailVideoId": str,
    "dxmPdfUrl": str,
    "dxmState": str,
    "dxmVideoId": str,
    "freightTemplateId": str,
    "fullCid": str,
    "goodsModel": str,
    "id": str,
    "instructionsId": str,
    "instructionsName": str,
    "instructionsTranslateId": str,
    "mainProductSkuSpecReqs": str,
    "materialImgUrl": str,
    "optionValue": str,
    "originFileUrl": str,
    "outerGoodsUrl": str,
    "personalizationSwitch": int,
    "productId": str,
    "productName": str,
    "productNameI18n": str,
    "productOrigin": str,
    "productSaleExtAttrReq": str,
    "productSemiManagedReq": str,
    "productWarehouseRouteReq": str,
    "qualifiedEn": str,
    "region1ShortNames": str,
    "region2Id": str,
    "sensitiveAttr": str,
    "shipmentLimitSecond": int,
    "shopId": str,
    "sizeCharts": str,
    "sizeTemplateIds": str,
    "sourceUrl": str,
    "variationListStr": str,
}


# 保存/发布接口路径里那段构建期固定 token（来自前端 service 层）
SAVE_TOKEN = "3f8c2a97"

# save_product 的 op 取值（前端菜单源码）
OP_SAVE = 1          # 保存
OP_PUBLISH = 2       # 立即发布
OP_SAVE_TO_WAIT = 3  # 保存并移入待发布
OP_TIMING = 5        # 定时发布


# 店小秘的返回外壳是 {code, msg, msgCode, data}。
# 实测（2026-10-14，未登录会话）：{"code": 2001, "msg": "验证失败了", "msgCode": null, "data": null}
BAD_CODES = {"401", "403", "-1", "2001"}
BAD_MSG_HINTS = ("验证失败", "请先登录", "未登录", "登录过期", "登录失效", "登陆失效")


def sample_of(data: Any, *, limit: int = 12) -> str:
    """把响应压成"能看懂结构"的一行：简单字段给值，复杂字段给键名/类型。"""
    if not isinstance(data, dict):
        return type(data).__name__
    parts: list[str] = []
    for key, value in list(data.items())[:limit]:
        if isinstance(value, bool) or isinstance(value, (int, float)):
            parts.append(f"{key}={value}")
        elif isinstance(value, str):
            parts.append(f"{key}={value[:30]!r}")
        elif isinstance(value, dict):
            parts.append(f"{key}={{ {','.join(list(value)[:8])} }}")
        elif isinstance(value, list):
            first = value[0] if value else None
            hint = ",".join(list(first)[:8]) if isinstance(first, dict) else type(first).__name__
            parts.append(f"{key}=[{hint}]")
        else:
            parts.append(f"{key}={type(value).__name__}")
    return "; ".join(parts)[:500]


def verdict_of(data: Any) -> tuple[bool | None, str]:
    """判断一个响应是"成功"还是"未登录"。返回 (True/False/None, 原因)。

    None = 看不出来（此时按可用处理，但把结构带出去让人判断）。
    规则：只要有明确的否定信号就判否；全都肯定才判是。
    """
    if not isinstance(data, dict):
        return None, ""

    negative: list[str] = []

    if data.get("success") is False:
        negative.append("success=false")

    code = str(data.get("code", "")).strip()
    if code in BAD_CODES:
        negative.append(f"code={code}")

    msg = str(data.get("msg") or data.get("message") or "")
    for hint in BAD_MSG_HINTS:
        if hint in msg:
            negative.append(f"msg 含「{hint}」")
            break

    # data=null 且带 code 字段：店小秘未登录的形态
    if "code" in data and data.get("data") is None:
        negative.append("data 为空")

    if negative:
        return False, "、".join(negative)

    if data.get("success") is True:
        return True, ""
    if "code" in data and data.get("data") is not None:
        return True, ""
    return None, ""


class DxmClient:
    """带登录态的店小秘接口客户端。用 with 管理连接池。"""

    def __init__(
        self,
        cfg: Config,
        shop: str,
        *,
        session: ShopSession | None = None,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.cfg = cfg
        self.shop = shop
        self.session = session or load_session(cfg, shop)
        if self.session is None:
            raise NotLoggedIn(
                f"店铺「{shop}」没有登录态。先跑：temu-bot login --shop \"{shop}\""
            )
        if self.session.looks_expired():
            raise NotLoggedIn(
                f"店铺「{shop}」的登录态已过期（{self.session_path}）。重跑 login 再试。"
            )
        self._client = self._build_client(timeout=timeout, transport=transport)

    # ------------------------------------------------------------ 底层
    @property
    def session_path(self):
        return session_path(self.cfg, self.shop)

    def _build_client(self, *, timeout: float, transport: httpx.BaseTransport | None) -> httpx.Client:
        headers = {
            "accept": "application/json, text/plain, */*",
            "accept-language": "zh-CN,zh;q=0.9",
            **{k: v for k, v in (self.session.headers or {}).items() if v},
        }
        client = httpx.Client(
            base_url=BASE_URL,
            timeout=timeout,
            follow_redirects=True,
            headers=headers,
            transport=transport,
        )
        for cookie in self.session.cookies:
            name = cookie.get("name")
            if not name:
                continue
            try:
                client.cookies.set(
                    name,
                    cookie.get("value", ""),
                    domain=cookie.get("domain") or None,
                    path=cookie.get("path") or "/",
                )
            except Exception as exc:  # noqa: BLE001 - 个别 cookie 域不合法时跳过，不拖垮整体
                log.debug("跳过 cookie %s：%s", name, exc)
        return client

    def __enter__(self) -> "DxmClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:  # noqa: BLE001
            pass

    def _parse(self, response: httpx.Response, what: str) -> Any:
        if response.status_code >= 400:
            raise DxmError(f"{what} HTTP {response.status_code}：{response.text[:300]}")

        text = response.text
        stripped = text.lstrip()
        if stripped.startswith("<"):
            # 店小秘未登录时会返回 HTML 登录页
            raise NotLoggedIn(f"{what} 返回了 HTML（多半是登录态失效）：{text[:200]}")

        try:
            return response.json()
        except ValueError as exc:
            raise DxmError(f"{what} 返回的不是 JSON：{text[:300]}") from exc

    def get_json(self, path: str, params: dict[str, Any] | None = None, *, what: str = "") -> Any:
        what = what or f"GET {path}"
        log.debug("→ %s %s", what, params or "")
        response = self._client.get(path, params=params)
        return self._parse(response, what)

    def post_json(
        self,
        path: str,
        *,
        data: dict[str, Any] | None = None,
        json: Any = None,
        what: str = "",
    ) -> Any:
        what = what or f"POST {path}"
        log.debug("→ %s", what)
        response = self._client.post(path, data=data, json=json)
        return self._parse(response, what)

    # ------------------------------------------------------------ 已知端点
    def user_in(self) -> Any:
        """当前登录账号/店铺信息（健康检查也用它）。"""
        return self.get_json("/api/userIn.json", what="userIn")

    def user_info(self) -> Any:
        return self.get_json("/api/userInfo.json", what="userInfo")

    def health_check(self) -> dict[str, Any]:
        """探活：能拿到账号信息才算登录态可用。

        三种失败形态都要认出来：
        1) 返回 HTML 登录页 → NotLoggedIn
        2) 返回 JSON 但 code/msg/data 明确表示未登录（如 code=2001「验证失败了」）
        3) HTTP 错误
        """
        try:
            data = self.user_in()
        except NotLoggedIn as exc:
            return {"ok": False, "reason": "登录态失效", "detail": str(exc)[:200]}
        except DxmError as exc:
            return {"ok": False, "reason": "接口异常", "detail": str(exc)[:200]}

        sample = sample_of(data)
        verdict, why = verdict_of(data)

        if verdict is False:
            return {"ok": False, "reason": "接口返回未登录", "detail": why, "sample": sample}

        if verdict is None:
            return {
                "ok": True,
                "reason": "无法从响应结构判断登录状态（按可用处理）",
                "data_keys": sorted(data)[:15] if isinstance(data, dict) else [],
                "sample": sample,
            }

        return {
            "ok": True,
            "reason": "",
            "data_keys": sorted(data)[:15] if isinstance(data, dict) else [],
            "sample": sample,
        }

    def product_page_list(self, **payload: Any) -> Any:
        """半托管商品列表分页（原始返回）。载荷字段已实测确认。"""
        return self.post_json("/api/popTemuProduct/pageList.json", data=payload, what="popTemu.pageList")

    # ------------------------------------------------------------ 列表
    DEFAULT_LIST_PARAMS: dict[str, Any] = {
        "pageNo": 1,
        "pageSize": 50,
        "searchType": 0,
        "searchValue": "",
        "productSearchType": 1,
        "shopId": -1,
        "dxmState": "offline",
        "site": 0,
        "sortName": "2",
        "sortValue": 2,
    }

    def list_products(
        self,
        *,
        dxm_state: str = "offline",
        page_no: int = 1,
        page_size: int = 50,
        shop_id: int | str = -1,
        search_value: str = "",
        search_type: int = 0,
        product_search_type: int = 1,
        site: int = 0,
        sort_name: str = "2",
        sort_value: int = 2,
        **extra: Any,
    ) -> dict[str, Any]:
        """待发布/在线商品列表。dxm_state: offline(待发布) / online(在线) / draft(草稿)。

        返回 data.page：{list: [...], totalSize, totalPage, pageNo, pageSize}
        """
        payload = {
            **self.DEFAULT_LIST_PARAMS,
            "dxmState": dxm_state,
            "pageNo": page_no,
            "pageSize": page_size,
            "shopId": shop_id,
            "searchValue": search_value,
            "searchType": search_type,
            "productSearchType": product_search_type,
            "site": site,
            "sortName": sort_name,
            "sortValue": sort_value,
            **extra,
        }
        data = self.product_page_list(**payload)
        return (data or {}).get("data", {}) or {}

    def iter_products(self, *, max_items: int | None = None, **kwargs: Any):
        """翻页遍历商品。"""
        page_no = int(kwargs.pop("page_no", 1))
        page_size = int(kwargs.pop("page_size", 50))
        seen = 0
        while True:
            data = self.list_products(page_no=page_no, page_size=page_size, **kwargs)
            page = (data or {}).get("page") or {}
            rows = page.get("list") or []
            if not rows:
                return
            for row in rows:
                yield row
                seen += 1
                if max_items is not None and seen >= max_items:
                    return
            if page_no >= int(page.get("totalPage") or 1):
                return
            page_no += 1

    def offline_counts(self, **payload: Any) -> Any:
        """待发布数量。"""
        return self.post_json("/api/popTemuProduct/getOfflineCounts.json", data=payload, what="offlineCounts")

    def online_counts(self, **payload: Any) -> Any:
        """在线数量。"""
        return self.post_json("/api/popTemuProduct/getOnlineCounts.json", data=payload, what="onlineCounts")

    # ------------------------------------------------------------ 详情
    def product_edit(self, product_id: str) -> Any:
        """读商品详情（含标题/图片/SKU，用来做模板基线）。"""
        return self.get_json(
            "/api/popTemuProduct/edit.json",
            params={"id": product_id},
            what=f"popTemu.edit({product_id})",
        )

    # ------------------------------------------------------------ 账号/店铺/计费
    def shops(self) -> list[dict[str, Any]]:
        """从 userIn 的 shopMap 里取出店铺列表（店铺名 ↔ 店小秘 shopId 的映射）。"""
        data = self.user_in()
        payload = (data or {}).get("data") or {}
        shop_map = payload.get("shopMap") or {}
        shops: list[dict[str, Any]] = []
        for key, value in shop_map.items():
            if isinstance(value, dict):
                shops.append({"id": value.get("idStr") or value.get("id") or key, **value})
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        shops.append({"id": item.get("idStr") or item.get("id") or key, **item})
                    else:
                        shops.append({"id": item, "name": str(item), "platform": key})
            else:
                shops.append({"id": key, "name": str(value), "platform": key})
        return shops

    def verify_shop(self, expected: str | list[str]) -> ShopCheck:
        """核对当前登录态里有没有表里要上的那个店。

        业务规则（用户明确要求）：上架时按表格的店铺名人工切换店铺，
        工具负责核对——不一致必须拦下来提醒，不能继续铺货。
        """
        wanted = [expected] if isinstance(expected, str) else list(expected)
        wanted_norm = [_norm(w) for w in wanted if str(w).strip()]

        shops = self.shops()
        # 只报真店铺，别把「手工订单」那种占位桶也列出来添乱
        available = [str(s.get("name") or "") for s in shops if _is_real_shop(s)]

        for shop in shops:
            name = str(shop.get("name") or "")
            if _norm(name) in wanted_norm:
                return ShopCheck(
                    expected=wanted,
                    ok=True,
                    matched_name=name,
                    matched_id=str(shop.get("id")),
                    available=available,
                )

        return ShopCheck(expected=wanted, ok=False, available=available)

    def assert_shop(self, expected: str | list[str]) -> ShopCheck:
        """核对不通过就抛 ShopMismatch。"""
        result = self.verify_shop(expected)
        if not result.ok:
            raise ShopMismatch(result.message())
        return result

    def find_shop_id(self, name: str) -> str | None:
        """按店铺名找店小秘 shopId（大小写/空格无关）。"""
        target = name.strip().lower()
        for shop in self.shops():
            for key in ("name", "shopName", "shop_name", "title"):
                if str(shop.get(key, "")).strip().lower() == target:
                    return str(shop.get("id"))
        # 退化：模糊包含
        for shop in self.shops():
            if target and target in str(shop.get("name", "")).strip().lower():
                return str(shop.get("id"))
        return None

    def midou_balance(self, types: str = "2,3,4") -> dict[str, Any]:
        """米豆余额（按次计费服务的额度）。

        注意：**复制商品（`copy_product`）实测不扣这里的额度**——复制 6 次前后
        所有桶都没变。所以别把"复制会扣米豆"当作前提。
        """
        data = self.post_json(
            "/api/midou/valueService/balanceView.json", data={"types": types}, what="midou.balance"
        )
        return (data or {}).get("data", {}) or {}

    # ------------------------------------------------------------ 图片空间
    def album_index(self, **payload: Any) -> Any:
        """图片空间：列表与余量（原工具调用 2574 次，含余量检查）。"""
        return self.get_json("/api/album/index.json", params=payload or None, what="album.index")

    def cos_get_sign(self, **payload: Any) -> Any:
        """取对象存储上传签名。参数待录制确认。"""
        return self.post_json("/api/cos/getSign.json", data=payload, what="cos.getSign")

    def cos_callback(self, **payload: Any) -> Any:
        """图片上传完成后的回调登记。参数待录制确认。"""
        return self.post_json("/api/cos/cosDxmCallBack.json", data=payload, what="cos.callback")

    def upload_from_net(self, ids: str) -> Any:
        """从图片 URL 直接入库（避免本地 PUT）。"""
        return self.post_json(
            "/api/album/toUploadPicFromNet.json", data={"ids": ids}, what="album.fromNet"
        )

    # ------------------------------------------------------------ 图片上传三步
    # 流程（从店小秘前端 JS 里挖出来的，见 docs/图片上传.md）：
    #   1) cos_get_sign_for(bucket, region, fileName) → 拿到 url + sign + fileId
    #   2) 把文件 PUT 到 https:<url>，带 Authorization: <sign>
    #   3) cos_register(...) 回调登记 → 得到能在商品里用的图片地址

    def cos_get_sign_for(
        self,
        file_name: str,
        *,
        bucket: str = COS_BUCKET,
        region: str = COS_REGION,
    ) -> dict[str, Any]:
        """取上传签名。返回 {sign, url, fileId}。"""
        data = self.cos_get_sign(bucket=bucket, region=region, fileName=file_name)
        payload = (data or {}).get("data") or {}
        if (data or {}).get("code") != 0 or not payload.get("sign"):
            raise DxmError(f"取上传签名失败：{(data or {}).get('msg') or data}")
        return payload

    def cos_put(self, sign: str, url: str, content: bytes, content_type: str) -> str:
        """把文件 PUT 到对象存储。返回可直接访问的 https 地址。"""
        target = url if url.startswith(("http://", "https://")) else "https:" + url
        response = httpx.put(
            target,
            content=content,
            headers={"Authorization": sign, "content-type": content_type},
            timeout=UPLOAD_TIMEOUT,
        )
        if response.status_code >= 400:
            raise DxmError(f"上传到对象存储失败 HTTP {response.status_code}：{response.text[:200]}")
        return target

    def cos_register(
        self,
        *,
        file_id: str,
        file_name: str,
        file_size: int,
        bucket: str = COS_BUCKET,
        is_need_tree: int = 1,
        full_cid: str = "",
    ) -> dict[str, Any]:
        """回调登记，把刚上传的文件挂进图片空间。返回接口的 data。"""
        data = self.cos_callback(
            bucket=bucket,
            fileId=file_id,
            fileName=file_name,
            isNeedTree=is_need_tree,
            fileSize=file_size,
            fullCid=full_cid,
        )
        if (data or {}).get("code") != 0:
            raise DxmError(f"图片登记失败：{(data or {}).get('msg') or data}")
        return (data or {}).get("data") or {}

    def upload_image(self, path: Path | str, *, bucket: str = COS_BUCKET) -> dict[str, Any]:
        """把一个本地图片文件传上去：签名 → PUT → 登记。返回登记结果。"""
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"找不到要上传的图片：{path}")

        content = path.read_bytes()
        content_type = _guess_image_mime(path.suffix)
        signed = self.cos_get_sign_for(path.name, bucket=bucket)
        self.cos_put(signed["sign"], signed["url"], content, content_type)
        result = self.cos_register(
            file_id=signed.get("fileId", ""),
            file_name=path.name,
            file_size=len(content),
            bucket=bucket,
        )
        log.info("已上传图片 %s（%d 字节）", path.name, len(content))
        return {**result, "fileId": signed.get("fileId", ""), "cosUrl": signed.get("url", "")}

    @staticmethod
    def build_save_payload(product: dict[str, Any], op: int = OP_SAVE) -> dict[str, Any]:
        """把 edit.json 的商品对象压成店小秘保存接口要的那种扁平载荷。

        字段表与类型**全部来自抓包实测**（2026-10-15 捕获的真实 choiceSave.txt）：
        39 个键，其中 38 个都能在 data.product 里找到，只是多数要转成字符串。
        """
        payload: dict[str, Any] = {}
        for key, kind in SAVE_FIELD_TYPES.items():
            value = product.get(key)
            if kind is bool:
                payload[key] = bool(value)
            elif kind is int:
                try:
                    payload[key] = int(value) if value not in (None, "") else 0
                except (TypeError, ValueError):
                    payload[key] = 0
            else:
                payload[key] = "" if value is None else str(value)
        payload["op"] = op
        return payload

    @staticmethod
    def build_save_archive(product: dict[str, Any]) -> bytes:
        """把商品对象打包成店小秘要的那种 zip（内含 choiceSave.txt）。

        实测抓包（2026-10-15）：multipart 里的 file 字段不是纯文本，而是一个
        **deflate 压缩的 zip**，里面唯一的条目就叫 choiceSave.txt。
        早先直接传原始 JSON 会得到「保存对象转换异常」。
        """
        payload = json.dumps(product, ensure_ascii=False).encode("utf-8")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("choiceSave.txt", payload)
        return buffer.getvalue()

    def save_product(self, product: dict[str, Any], op: int = OP_SAVE, *, path: str | None = None) -> Any:
        """保存商品（前端"保存/立即发布/保存并移入待发布"都走这一个接口）。

        来自前端 service 层，并已按抓包校准：
            file = 一个 zip（内含 choiceSave.txt = 商品对象 JSON），字段名 file、文件名 blob
            op   = 1 保存 / 2 立即发布 / 3 保存并移入待发布 / 5 定时发布
        """
        target = path or f"/api/popTemuProduct/add{SAVE_TOKEN}.json"
        record = self.build_save_payload(product, op)      # 先压成保存接口要的 39 键
        archive = self.build_save_archive(record)          # 再 zip 成 choiceSave.txt
        files = {"file": ("blob", archive, "application/octet-stream")}
        response = self._client.post(target, files=files, data={"op": str(op)})
        return self._parse(response, f"save(op={op})")

    # ------------------------------------------------------------ 搬家 / 发布
    def copy_product(
        self,
        product_id: str,
        shop_ids: str | int,
        *,
        platform: str = "popTemu",
        hide_price: str = "0",
    ) -> Any:
        """复制商品（搬家）。

        form 参数来自前端「选择店铺」弹窗源码：
            {shopIds: 选中店铺.join(","), productId, platform, hidePrice:"0"}
        成功后商品出现在「待发布」列表。

        **不扣米豆**（实测：连续复制 6 次，`balanceView` 所有桶纹丝不动）。
        这条以前写的是"扣米豆"，是没验证过的假设，已按实测改掉。
        真正要防的是**重复铺货**——同一行忘了写回商品ID 再跑一次，会多出一个重复商品。
        """
        payload = {
            "shopIds": shop_ids,
            "productId": product_id,
            "platform": platform,
            "hidePrice": hide_price,
        }
        return self.post_json("/api/banjia/copyProduct.json", data=payload, what="banjia.copyProduct")

    @staticmethod
    def ids_param(ids: list[str] | str) -> dict[str, str]:
        """批量接口的 ids 参数：逗号分隔。"""
        if isinstance(ids, str):
            return {"ids": ids}
        return {"ids": ",".join(str(i) for i in ids)}

    def batch_publish(self, ids: list[str] | str, *, path: str | None = None) -> Any:
        """发布商品（前端的"批量发布"，ids 逗号分隔）。

        半托管的路径有两种命名空间，默认用新式；旧式见 pddkjProduct/batchPublish3f8c2a97.json。
        """
        target = path or "/api/product/popTemu/batchPublish.json"
        return self.post_json(target, data=self.ids_param(ids), what=f"publish({target})")

    def batch_to_wait_publish(self, ids: list[str] | str) -> Any:
        """批量移入待发布。"""
        return self.post_json(
            "/api/product/popTemu/batchToWaitPublish.json",
            data=self.ids_param(ids),
            what="batchToWaitPublish",
        )

    def batch_delete(self, ids: list[str] | str) -> Any:
        """批量删除商品。"""
        return self.post_json(
            "/api/product/popTemu/batchDelete.json", data=self.ids_param(ids), what="batchDelete"
        )
