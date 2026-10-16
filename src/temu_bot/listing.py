"""上架主流程：表格一行 → 核对店铺 → 找模板 → 复制 → 改标题/图 → 保存。

设计原则：
- **默认干跑**：只跑只读步骤，把"接下来会发什么请求、会改什么字段"全列出来。
  真正写入必须显式 live=True。
- 店铺不一致**立刻中止**（用户明确要求：按表格店铺名人工切换，工具负责核对）。
- 每一步都有结构化结果，便于 CLI/日志/后续控制台复用。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from temu_bot.config import Config, ShopCfg
from temu_bot.imaging.pipeline import PrepResult, process_row
from temu_bot.imaging.swap import (
    describe_product_images,
    patch_product_images,
    showcase_images,
)
from temu_bot.dianxiaomi.client import (
    OP_PUBLISH,
    OP_SAVE,
    OP_SAVE_TO_WAIT,
    OP_TIMING,
    DxmClient,
    DxmError,
    NotLoggedIn,
    ShopMismatch,
    find_login_for_shop,
)
from temu_bot.product import (
    fix_en_title,
    get_en_title,
    normalize_en_title,
    set_en_title,
    set_sku_code,
    sku_codes,
)
from temu_bot.sheet.schema import SheetRow, iter_missing_files
from temu_bot.sheet.writeback import statuses_of

log = logging.getLogger(__name__)

# 平台商品 ID 搜索（实测确认：searchType=3 就是按 SPU/平台商品ID 搜）
SEARCH_BY_PLATFORM_ID = 3

# op 的人话解释（日志/干跑输出里用）
OP_LABELS: dict[int, str] = {
    OP_SAVE: "仅保存",
    OP_PUBLISH: "立即发布",
    OP_SAVE_TO_WAIT: "移入待发布",
    OP_TIMING: "定时发布",
}


class Status(str, Enum):
    OK = "ok"
    DRY = "dry"          # 干跑：本来会执行，这次没执行
    SKIP = "skip"
    FAIL = "fail"


@dataclass(slots=True)
class StepResult:
    name: str
    status: Status
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def line(self) -> str:
        icon = {Status.OK: "✅", Status.DRY: "🔸", Status.SKIP: "⏭", Status.FAIL: "❌"}[self.status]
        return f"{icon} {self.name}：{self.detail}"


WRITEBACK_STEP = "WRITEBACK"


@dataclass(slots=True)
class ListingOutcome:
    row_key: str
    shop: str
    steps: list[StepResult] = field(default_factory=list)
    product_id: str = ""
    # None = 这次不涉及回写（干跑 / 被取消）；True/False = 写回结果
    writeback_ok: bool | None = None

    @property
    def failed(self) -> bool:
        """**上架**是不是失败了。

        回写失败**不算上架失败**——商品已经建好了，把它算成"失败"会让人以为要重跑，
        而那正好会造成重复铺货。回写的问题看 `writeback_failed` 和 WRITEBACK 那一步。
        """
        return any(
            step.status is Status.FAIL and step.name != WRITEBACK_STEP for step in self.steps
        )

    @property
    def writeback_failed(self) -> bool:
        return self.writeback_ok is False

    def as_dict(self) -> dict[str, Any]:
        return {
            "row_key": self.row_key,
            "shop": self.shop,
            "product_id": self.product_id,
            "failed": self.failed,
            "writeback_ok": self.writeback_ok,
            "steps": [
                {"name": s.name, "status": s.status.value, "detail": s.detail, **s.data}
                for s in self.steps
            ],
        }


def _clip(text: str, limit: int) -> str:
    """只为在日志里好看而截断——**截断处加省略号**。

    不加省略号的话，`'…Jingle B'` 看着像标题真被砍成那样了，会吓人一跳。
    """
    return text if len(text) <= limit else text[: limit - 1] + "…"


class ListingPipeline:
    """一行商品的上架流程。live=False 时只跑只读步骤。"""

    def __init__(
        self,
        cfg: Config,
        row: SheetRow,
        *,
        login: str | None = None,
        live: bool = False,
        op: int = OP_SAVE,
        upload_images: bool = False,
        images_dir: Path | None = None,
        client_factory: Any = None,
        on_step: Callable[[StepResult], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
        swap_images: bool | None = None,
        on_writeback: Callable[["ListingPipeline"], Any] | None = None,
    ) -> None:
        self.cfg = cfg
        self.row = row
        # 登录身份：给了就用给的；**没给就等 SHOP_CHECK 从表格店铺名自动认**
        # （用户 2026-10-16：不要再让人手选身份）
        self.login = login or ""
        self._login_source = ""
        self.live = live
        self.op = op
        self.upload_images = upload_images
        # 图片文件夹：命令行给了就用它，否则用表里第 1 列
        self.images_dir = Path(images_dir) if images_dir else None
        # 每产生一步就回调一次：Web 控制台靠它做实时进度（CLI 不传，行为不变）
        self.on_step = on_step
        # 取消信号：**只在动写操作之前**会被认（见 _cancelled）
        self.should_cancel = should_cancel
        # 要不要换图？
        #   新建的行（表里没商品ID）→ **必须换**，否则存下去的是模板的图；
        #   已有的商品（表里有商品ID）→ 默认不换，它是这一行上过的商品，
        #     重试发布没必要再往图片空间塞一遍同样的图。
        # 传 swap_images=True/False 可强制覆盖。
        self.swap_images = (not row.product_id) if swap_images is None else bool(swap_images)
        # 回写表格：不给就用内置的默认实现（一次一行，自带备份）。
        # 批量跑时上层会注入自己的（一整批只备份一次），见 web/jobs.py。
        self.on_writeback = on_writeback
        # 是不是"还没动任何写操作就被取消了"——那种情况没发生过任何事，
        # 不该往表格里写状态
        self._cancelled_early = False
        self._wrote = False
        # 复制前待发布里已有的商品 id（认"多出来的那个"就是刚复制的新商品）。
        # None = 还没取过；空 set = 取过但失败/本来就是空的。
        self._offline_before: set[str] | None = None
        self.outcome = ListingOutcome(row_key=row.row_key, shop=row.shop)
        # 注入了假客户端就用假的；否则**等认完登录身份再建真客户端**
        self._client_factory = client_factory
        self._client: DxmClient | None = None
        self._shop_cfg: ShopCfg | None = None
        # SHOP_CHECK 核对出来的店小秘 shopId。**以登录账号实时读到的为准**，
        # 不看配置里的静态映射（店铺会变）。后面 COPY / FIND_TEMPLATE 都用它。
        self._shop_id: str = ""
        self._media: PrepResult | None = None
        self._template: dict[str, Any] | None = None
        # 上传后的图片地址（主图可能多张，素材图一张）
        self._uploaded_main: list[str] = []
        self._uploaded_material: str = ""

    # ------------------------------------------------------------ 基础设施
    def _make_client(self) -> DxmClient:
        """建客户端。**必须在认完登录身份之后调**（没给 login 时它就是待定的）。"""
        if self._client_factory is not None:
            return self._client_factory()
        return DxmClient(self.cfg, self.login)

    @property
    def _apply_source(self) -> str:
        """APPLY 读的是谁：表里已有的商品，还是刚复制出来的新商品。"""
        return "表里已有的商品" if self.row.product_id else "刚复制出来的新商品"

    def _add(self, name: str, status: Status, detail: str = "", **data: Any) -> StepResult:
        step = StepResult(name=name, status=status, detail=detail, data=data)
        self.outcome.steps.append(step)
        log.info("[%s] %s：%s", status.value, name, detail)
        if self.on_step is not None:
            try:
                self.on_step(step)
            except Exception:  # noqa: BLE001
                # 进度回调是"旁路"，它出问题不能把上架本身带崩
                log.warning("步骤回调失败（已忽略）", exc_info=True)
        return step

    # ------------------------------------------------------------ 各步骤
    def step_precheck(self) -> bool:
        """只读：这一行的输入齐不齐。"""
        problems: list[str] = []
        if not self.row.folder:
            problems.append("表格里没有「文件夹路径」")
        else:
            folder = Path(self.row.folder)
            if not folder.exists():
                problems.append(f"文件夹不存在：{folder}")
        if not self.row.main_image:
            problems.append("没有「主图」")
        if not self.row.material_image:
            # 没有素材图 → 素材图位会留着模板的那张，上出来是个"半对"的商品。
            # 按收集图片时定的规矩：缺素材图的行本来就不该算一行。
            problems.append("没有「素材图」（缺素材图的行请先跳过，别硬上）")
        if not self.row.sku_prefix_code:
            # 没有它就只能沿用模板的 SKU 货号 → 一批商品全挂同一个货号，
            # 而用户明确要求"用表里的、不要用模板的"。所以宁可不跑。
            problems.append(
                "没有「一键生成SKU货号前缀编码」（第 9 列）——上架要用它填 SKU 货号，"
                "不能沿用模板的（那会让一批商品共用一个货号）。"
            )
        if not self.row.template_spu:
            problems.append("没有「模板产品SPUID」")
        if not self.row.title_cn:
            problems.append("没有「中文标题」")

        missing = iter_missing_files(self.row)
        if missing:
            problems.append(f"缺图片文件：{missing}")

        if problems:
            self._add("PRECHECK", Status.FAIL, "；".join(problems))
            return False

        self._add(
            "PRECHECK",
            Status.OK,
            f"{self.row.main_image}｜素材图 {self.row.material_image or '(无)'}｜"
            f"模板SPU {self.row.template_spu}｜标题「{self.row.title_cn[:20]}」",
        )
        return True

    def step_media(self) -> bool:
        """本地：按 Temu 规格出图（**原图只读**）。干跑只出计划，live 才落盘。

        放在碰网络之前——图片不合格是本地就能查出来的事，别等平台打回。
        """
        result: PrepResult = process_row(
            self.cfg, self.row, dry_run=not self.live, images_dir=self.images_dir
        )
        self._media = result

        if not result.items:
            self._add("MEDIA", Status.SKIP, "这一行没有图片列")
            return True

        detail = "；".join(item.line() for item in result.items)
        if result.failed:
            self._add("MEDIA", Status.FAIL, detail)
            return False

        if self.live:
            self._add(
                "MEDIA",
                Status.OK,
                f"{detail}（产出目录 {result.out_dir}）",
                out_dir=str(result.out_dir),
                rewrites=result.rewrites,
            )
        else:
            self._add(
                "MEDIA",
                Status.DRY,
                f"{detail}（干跑：届时写到 {result.out_dir}）",
                out_dir=str(result.out_dir),
                rewrites=result.rewrites,
            )
        return True

    def _resolve_login(self) -> bool:
        """**表里这家店该用哪个登录身份？** 用户不手选了，程序自己认。

        拿表格第 10 列的店名，挨个问可用登录身份的账号里有没有这家店
        （实时读 `userIn`），谁有就是谁。找不到就停。

        显式传了 `login`（CLI `--shop` / 测试）就用它，不做搜索——
        但要能对上，否则同样停下。
        """
        if self.login:
            return True

        lookup = find_login_for_shop(self.cfg, self.row.shop)
        if not lookup.ok:
            self._add(
                "SHOP_CHECK",
                Status.FAIL,
                f"{lookup.message()}→ 请到店小秘里确认这家店在哪个账号下，"
                f"登录好之后把浏览器窗口关掉，再重新开始。",
                expected=self.row.shop,
                available=lookup.available,
                scanned=lookup.scanned,
            )
            return False

        self.login = lookup.login
        self._login_source = "自动认出来的"
        log.info("表里「%s」→ 用登录身份「%s」", self.row.shop, self.login)
        return True

    def step_shop_check(self) -> bool:
        """只读：表里这行要上的店，必须是**有某个登录身份的账号里真的有的店**。

        用户 2026-10-16 明确：**店铺不固定，会随情况变化**，而且**不要再让人手选
        登录身份**。所以这里做两件事：

            表里写「X」→ ① 哪个登录身份的账号里有「X」？（自动认）
                        → ② 用那个身份再确认一遍，并拿到 shopId 给后面几步用

        一律**不看配置里的静态映射**（`config.shops` 只当提示，随时可能过期）。
        对不上就停，并把查过哪些身份、账号里实际有哪些店一并报出来。
        """
        if not self._resolve_login():
            return False

        try:
            self._client = self._make_client()
        except NotLoggedIn as exc:
            self._add("SHOP_CHECK", Status.FAIL, str(exc))
            return False

        try:
            result = self._client.verify_shop(self.row.shop)
        except DxmError as exc:
            self._add("SHOP_CHECK", Status.FAIL, f"核对店铺失败：{exc}")
            return False

        if not result.ok:
            available = "、".join(n for n in result.available if n.strip()) or "（一个都没读到）"
            self._add(
                "SHOP_CHECK",
                Status.FAIL,
                f"店铺不符，已中止：表里这行要上「{self.row.shop}」，"
                f"但登录身份「{self.login}」的账号里**没有**这家店。"
                f"账号里现有的店：{available}。"
                f"→ 请确认：① 这个身份该不该用（自动认错的话用 --shop 指明）；"
                f"② 表里第 10 列店铺名有没有写错。",
                expected=self.row.shop,
                available=result.available,
            )
            return False

        shop_id = str(result.matched_id)
        self._shop_id = shop_id
        self._shop_cfg = self.cfg.shop_or_default(self.row.shop)

        how = getattr(self, "_login_source", "") or "指定的登录身份"
        self._add(
            "SHOP_CHECK",
            Status.OK,
            f"表里「{self.row.shop}」↔ 登录身份「{self.login}」（{how}）↔ 账号里的店"
            f"「{result.matched_name}」(shopId={shop_id})",
            shop_id=shop_id,
            expected=self.row.shop,
            login=self.login,
        )
        return True

    def step_find_template(self) -> bool:
        """只读：用模板 SPU 定位模板商品，**并且必须落在这家店里**。

        用户 2026-10-16 明确：只要「表里的模板在登录的店铺里找得到」就行。
        所以搜索**限定在 SHOP_CHECK 核对出来的那个 shopId**，
        找到之后再拿商品自己的 `shopId` 复核一遍——万一过滤参数被忽略，
        也不会把别家店的同 SPU 商品当成模板（那会复制出完全不相干的东西）。
        """
        assert self._client is not None
        spu = self.row.template_spu
        shop_id = self._shop_id

        for state in ("online", "offline"):
            data = self._client.list_products(
                dxm_state=state,
                page_size=5,
                search_type=SEARCH_BY_PLATFORM_ID,
                search_value=spu,
                shop_id=shop_id or -1,
            )
            for row in ((data.get("page") or {}).get("list")) or []:
                if shop_id and str(row.get("shopId")) != shop_id:
                    # 兜底：过滤没生效时，别把别家店的商品当成模板
                    continue
                self._template = row
                self._add(
                    "FIND_TEMPLATE",
                    Status.OK,
                    f"在「{self.row.shop}」的「{state}」里找到模板："
                    f"{str(row.get('productName'))[:30]}"
                    f"（id={row.get('id')}，状态={row.get('dxmState')}）",
                    template_id=str(row.get("id")),
                    template_state=str(row.get("dxmState")),
                    shop_id=shop_id,
                )
                return True

        self._add(
            "FIND_TEMPLATE",
            Status.FAIL,
            f"在「{self.row.shop}」(shopId={shop_id}) 里**找不到**模板 SPU {spu}"
            f"（在线的、待发布的都搜过了）。→ 请确认：① 这个 SPU 确实是这家店的商品；"
            f"② 表里第 11 列模板 SPU 有没有写错；③ 登录身份有没有选对。",
            shop_id=shop_id,
            spu=spu,
        )
        return False

    def step_copy(self) -> bool:
        """写：复制模板 → 新商品进待发布。

        备注：**实测复制不扣米豆**（复制 6 次前后，`balanceView` 的 availableBalance、
        todayFreeBalance 和各服务 availableCount 一个都没变）。所以别再写"扣米豆"了。
        """
        assert self._client is not None and self._shop_cfg is not None

        # 表里已经有商品ID → 这行之前已经建过商品了（多半是"建好了但发布失败"）。
        # 再复制一份就是**重复铺货**，平台会罚——必须跳过，改用已有商品续跑。
        if self.row.product_id:
            self._add(
                "COPY",
                Status.SKIP,
                f"表里已有商品ID {self.row.product_id}，跳过复制"
                f"（再复制=重复铺货）。这行会改为在已有商品上续跑。",
                existing_product_id=self.row.product_id,
            )
            return True

        shop_id = None
        for step in self.outcome.steps:
            if step.name == "SHOP_CHECK":
                shop_id = step.data.get("shop_id")
        template_id = str((self._template or {}).get("id") or "")

        payload = {
            "shopIds": shop_id,
            "productId": template_id,
            "platform": "popTemu",
            "hidePrice": "0",
        }
        if not self.live:
            self._add(
                "COPY",
                Status.DRY,
                f"将 POST /api/banjia/copyProduct.json {payload}",
                payload=payload,
            )
            return True

        # 到这里就要真写了——从这一刻起不再受理取消请求（见 _cancelled）
        # 复制**之前**先记下待发布里已有哪些商品：等下靠"多出来的那个"认新商品。
        # 只按标题认会认错——上一行要是复制成功但改名失败，它就还挂着模板的标题，
        # 会被这一行当成"刚复制出来的"捡走，然后把这一行的图和标题写到它身上。
        if self._offline_before is None:
            self._offline_before = self._offline_ids()

        self._wrote = True
        try:
            result = self._client.copy_product(template_id, str(shop_id))
        except DxmError as exc:
            self._add("COPY", Status.FAIL, f"复制失败：{exc}")
            return False

        code = (result or {}).get("code")
        if code != 0:
            self._add("COPY", Status.FAIL, f"复制被拒：code={code} msg={(result or {}).get('msg')}")
            return False

        self._add("COPY", Status.OK, "复制成功，已放入待发布", payload=payload)
        return True

    def _offline_ids(self) -> set[str]:
        """待发布列表里现有哪些商品 id（只读，认新商品用）。"""
        assert self._client is not None
        try:
            data = self._client.list_products(dxm_state="offline", page_size=50)
        except DxmError as exc:
            log.warning("取待发布列表失败（改成按标题认新商品）：%s", exc)
            return set()
        rows = ((data.get("page") or {}).get("list")) or []
        return {str(r.get("id")) for r in rows if r.get("id")}

    def step_find_new(self) -> str | None:
        """只读：找出刚复制出来的新商品 id。"""
        assert self._client is not None

        # 表里已有商品ID：直接沿用，不去列表里翻（翻也可能翻错）
        if self.row.product_id:
            product_id = self.row.product_id
            self._add("FIND_NEW", Status.SKIP, f"沿用表里已有的商品ID {product_id}")
            self.outcome.product_id = product_id
            return product_id

        if not self.live:
            self._add(
                "FIND_NEW",
                Status.DRY,
                "（干跑）复制前先取待发布 id 快照，届时认「多出来的那一个」；"
                "多出 2 个以上就中止，不猜",
            )
            return None

        fresh = None
        if self._offline_before is not None:
            data = self._client.list_products(
                dxm_state="offline", page_size=50, sort_name="2", sort_value=2
            )
            rows = ((data.get("page") or {}).get("list")) or []
            added = [r for r in rows if str(r.get("id")) not in self._offline_before]
            if len(added) == 1:
                fresh = added[0]
                how = f"复制前没有、复制后多出来的那一个（{len(self._offline_before)} → {len(rows)}）"
            elif len(added) > 1:
                self._add(
                    "FIND_NEW",
                    Status.FAIL,
                    f"复制后待发布里多出了 {len(added)} 个商品，认不准哪个是这次的——"
                    f"已中止，请到店小秘待发布里人工看一眼再决定（这几行别重跑，会重复铺货）",
                )
                return None

        if fresh is None:
            # 没拿到复制前的快照 / 没认出唯一的新商品 → 退回按标题认（老办法）
            data = self._client.list_products(
                dxm_state="offline", page_size=10, sort_name="2", sort_value=2
            )
            rows = ((data.get("page") or {}).get("list")) or []
            template_name = str((self._template or {}).get("productName") or "")
            for row in rows:
                if str(row.get("productName") or "") == template_name:
                    fresh = row
                    how = f"按标题认出来的（标题还等于模板名 {template_name[:16]}）"
                    break

        if fresh is not None:
            product_id = str(fresh.get("id"))
            self._add("FIND_NEW", Status.OK, f"新商品 id={product_id}｜{how}")
            self.outcome.product_id = product_id
            return product_id

        self._add(
            "FIND_NEW",
            Status.FAIL,
            "复制成功但待发布列表里没找到新商品（原工具也专门处理过这种情况），需要重试或人工看一眼",
        )
        return None

    def _title_plan(self, product: dict[str, Any]) -> tuple[str, str, str, str]:
        """算出标题的目标值：返回 (before_cn, after_cn, after_en)。

        英文标题以表里填的为准；表里没填就退回模板自带的英文标题（做规范化清洗）。
        """
        before_cn = str(product.get("productName") or "")
        before_en = get_en_title(product)
        after_cn = self.row.title_cn or before_cn
        after_en = normalize_en_title(self.row.title_en or before_en) or normalize_en_title(before_en)
        return before_cn, before_en, after_cn, after_en

    def _title_diff(self, product: dict[str, Any]) -> str:
        before_cn, before_en, after_cn, after_en = self._title_plan(product)
        parts: list[str] = []
        if before_cn != after_cn:
            parts.append(f"中文标题 {_clip(before_cn, 30)!r} → {_clip(after_cn, 30)!r}")
        if before_en != after_en:
            parts.append(f"英文标题 {_clip(before_en, 46)!r} → {_clip(after_en, 46)!r}")
        return "；".join(parts)

    def step_upload_images(self) -> bool:
        """把这一行的图传到店小秘（主图 → 轮播/缩略图，素材图 → 素材图位）。

        只上传**已经出好的合规图**（`MEDIA` 的产出），原图不动。
        干跑只报告会传哪几张。
        """
        assert self._client is not None

        if not self.swap_images:
            self._add(
                "UPLOAD",
                Status.SKIP,
                f"表里已有商品ID {self.row.product_id}，这是给它重试/续跑，"
                f"不重复上传图片（要强制换图用 --swap-images）",
            )
            return True

        planned = self._media.items if self._media else []
        if not planned:
            self._add("UPLOAD", Status.SKIP, "这一行没有图片列，跳过上传")
            return True

        mains = [i for i in planned if i.slot == "main"]
        materials = [i for i in planned if i.slot == "material"]

        keep = self._template_showcase()
        keep_note = (
            f"；模板的 {len(keep)} 张展示图保留不动" if keep else "；模板没有展示图"
        )

        if not self.live:
            names = "、".join(i.dst.name for i in planned)
            self._add(
                "UPLOAD",
                Status.DRY,
                f"将上传 {len(planned)} 张（{names}）："
                f"主图 {len(mains)} 张 → 顶到轮播第 1 位 + SKU 缩略图；"
                f"素材图 {len(materials)} 张 → 素材图位{keep_note}",
                count=len(planned),
            )
            return True

        self._wrote = True
        try:
            for item in planned:
                if not item.dst.exists():
                    self._add("UPLOAD", Status.FAIL, f"要上传的文件不在了：{item.dst}")
                    return False
                uploaded = self._client.upload_image(item.dst)
                url = uploaded.get("cosUrl") or ""
                if not url:
                    self._add("UPLOAD", Status.FAIL, f"上传 {item.dst.name} 没拿到地址")
                    return False
                if not url.startswith(("http://", "https://")):
                    url = "https:" + url
                if item.slot == "main":
                    self._uploaded_main.append(url)
                elif item.slot == "material":
                    self._uploaded_material = url
        except DxmError as exc:
            self._add("UPLOAD", Status.FAIL, f"上传图片失败：{exc}")
            return False

        self._add(
            "UPLOAD",
            Status.OK,
            f"已上传 {len(planned)} 张：主图 {len(self._uploaded_main)} 张、"
            f"素材图 {'1 张' if self._uploaded_material else '无'}{keep_note}",
            count=len(planned),
            main=len(self._uploaded_main),
            material=1 if self._uploaded_material else 0,
        )
        return True

    def step_apply_fields(self, product_id: str) -> bool:
        """读新商品 → 改标题 → 保存。"""
        assert self._client is not None
        response = self._client.product_edit(product_id)
        product = (response.get("data") or {}).get("product") or {}
        if not product:
            self._add("APPLY", Status.FAIL, f"读不到商品对象（id={product_id}）")
            return False

        summary = self._title_diff(product)

        # SKU 货号：用表里第 9 列那个，**不要模板的**（模板的会让一批商品共用一个货号）。
        # 只动 SKU 级 extCode；SKC 级的保持模板的（对着老工具上架成功的商品核过）。
        sku_note = ""
        code = self.row.sku_prefix_code
        if code:
            changed, before = set_sku_code(product, sku_code=code)
            if changed:
                sku_note = f"SKU货号 {before or '(空)'} → {code}（{changed} 条）"

        # 换图：把 UPLOAD 拿到的地址写进商品对象。这一步必须在保存之前做，
        # 否则保存下去的仍旧是模板的图——那正是"标题对、图片错"的由来。
        image_notes: list[str] = []
        if self._uploaded_main or self._uploaded_material:
            # 只换主图：新主图顶到第 1 位，**模板的展示图保留不动**
            keep = self._template_showcase() if self._uploaded_main else []
            patch = patch_product_images(
                product,
                main_urls=self._uploaded_main,
                material_url=self._uploaded_material or None,
                keep_previews=keep if keep else None,
            )
            image_notes = patch.notes + [f"⚠ {w}" for w in patch.warnings]
            if not patch:
                self._add(
                    "APPLY",
                    Status.FAIL,
                    "上传成功了，但商品里找不到可写的图片字段——图片没换上去，"
                    "继续保存会得到「标题对、图片是模板的」错商品，已中止。",
                )
                return False

        # 标题没变、货号没变、也没换图、且用户只要"保存" → 真的没什么可做，跳过。
        # 但 op=2/3 时那个 op 本身就是动作（发布/移入待发布），必须发出去。
        if not summary and not sku_note and not image_notes and self.op == OP_SAVE:
            self._add("APPLY", Status.SKIP, "标题、货号与图片都无需修改，且只要求保存，跳过")
            return True

        parts = [p for p in (summary, sku_note, "；".join(image_notes)) if p]
        detail = "；".join(parts) or f"标题、货号与图片均未变，按 op={self.op}（{OP_LABELS.get(self.op, '?')}）提交"

        _, _, after_cn, after_en = self._title_plan(product)
        product["productName"] = after_cn
        set_en_title(product, after_en)

        if not self.live:
            self._add(
                "APPLY",
                Status.DRY,
                f"（干跑，读的是{self._apply_source}）将提交：{detail}",
                op=self.op,
            )
            return True

        try:
            result = self._client.save_product(product, op=self.op)
        except DxmError as exc:
            self._add("APPLY", Status.FAIL, f"保存失败：{exc}")
            return False

        if (result or {}).get("code") != 0:
            self._add(
                "APPLY", Status.FAIL, f"保存被拒：code={(result or {}).get('code')} msg={(result or {}).get('msg')}"
            )
            return False

        self._add("APPLY", Status.OK, f"已提交：{detail}")
        return True

    def step_apply_preview(self) -> bool:
        """干跑专用：拿模板商品当样本（**只读**），把"会改什么"提前算出来给你看。"""
        assert self._client is not None
        fallback = "（干跑）届时读新商品 → 改标题 → 保存"
        template_id = str((self._template or {}).get("id") or "")
        if not template_id:
            self._add("APPLY", Status.DRY, fallback)
            return True

        try:
            response = self._client.product_edit(template_id)
        except DxmError as exc:
            self._add("APPLY", Status.DRY, f"（干跑）预览读模板失败：{str(exc)[:120]}；{fallback}")
            return True

        product = (response.get("data") or {}).get("product") or {}
        if not product:
            self._add("APPLY", Status.DRY, f"（干跑）模板读不出商品对象；{fallback}")
            return True

        summary = self._title_diff(product) or "标题与模板一致，无需修改"
        code = self.row.sku_prefix_code
        before_code = "、".join(sku_codes(product)) or "(空)"
        if code and code != before_code:
            summary = f"{summary}；SKU货号 {before_code} → {code}"
        if self.swap_images:
            summary = f"{summary}；换图：{self._swap_plan_detail(product)}"
        self._add(
            "APPLY",
            Status.DRY,
            f"（干跑，以模板为样本）{summary}（op={self.op}）",
            op=self.op,
        )
        return True

    def _template_showcase(self) -> list[str]:
        """模板商品那几张**展示图**（轮播里去掉第 1 张主图之后的）。

        换图的规矩（用户 2026-10-16 定）：只把主图换掉，模板的展示图**保留不动**。
        所以保留的这几张要以**模板**为准——万一这个商品之前被换错过（比如展示图
        已经丢了），从它自己身上取就取不回来了。

        读一次模板（只读）。读不到就退化成"用这个商品自己现有的展示图"
        （`patch_product_images` 的 keep_previews=None 分支），不因此中止。
        """
        template_id = str((self._template or {}).get("id") or "")
        if not template_id or self._client is None:
            return []
        try:
            response = self._client.product_edit(template_id)
        except DxmError as exc:
            log.warning("读模板商品失败，展示图改用这个商品自己现有的：%s", exc)
            return []
        product = (response.get("data") or {}).get("product") or {}
        return showcase_images(product)

    def _swap_plan_detail(self, product: dict) -> str:
        """干跑时把"会把商品的哪些图片位换掉"说清楚（照着模板的字段结构算）。

        `product` 干跑时传进来的是**模板**（拿它当样本），所以这里的"现在有几张"
        就是模板的情况。
        """
        now = describe_product_images(product)
        planned = self._media.items if self._media else []
        mains = len([i for i in planned if i.slot == "main"])
        material = any(i.slot == "material" for i in planned)
        if not mains and not material:
            return "这一行没有图片可换"

        bits: list[str] = []
        if mains:
            keep = len(self._template_showcase()) or now["showcase_count"]
            after = mains + keep
            bits.append(
                f"轮播图 {now['preview_count']} 张 → {after} 张"
                f"（新主图 {mains} 张排第 1，保留展示图 {keep} 张；模板原来的主图删掉）"
            )
            bits.append(f"SKU 缩略图 {now['thumb_count']} 条 → 新主图")
        if material:
            bits.append("素材图 → 换")
        return "；".join(bits)

    def step_writeback(self) -> None:
        """把「商品ID / 状态 / 问题项」写回表格（**干跑不写**）。

        这一步是**防重复铺货的闸门**：表格里没有商品ID 的行，下次跑还是"新建"，
        会再复制一遍模板、再建一个重复商品。所以跑一行就锁一行，不等整批跑完。

        回写失败（表开着 Excel 等）**不会**让上架结果作废——商品已经建好了，
        抹掉只会更糟。失败会作为一步 ❌ 报出来，并在结果里给出补写命令。
        """
        if not self.live:
            self._add("WRITEBACK", Status.DRY, "（干跑）届时把商品ID 与状态写回表格")
            return
        if self._cancelled_early:
            self._add("WRITEBACK", Status.SKIP, "这一行被取消、什么都没做，不写表格")
            return

        from temu_bot.sheet.writeback import updates_for, write_back

        row = self.row
        # 表里本来就有商品ID 的行（重试/续跑），这次没拿到新ID 就别去动它
        product_id = self.outcome.product_id or row.product_id
        update = updates_for(
            row,
            product_id=product_id,
            failed=self.outcome.failed,
            steps=self.outcome.steps,
            statuses=statuses_of(self.cfg),
        )

        if self.on_writeback is not None:
            report = self.on_writeback(self, update)
        else:
            report = write_back(self.cfg, update)

        if report is not None and not report.ok:
            self._add("WRITEBACK", Status.FAIL, f"{report.message}｜补写：{self._writeback_hint()}")
            self.outcome.writeback_ok = False
            return

        detail = getattr(report, "message", "") or "已写回"
        self._add("WRITEBACK", Status.OK, f"已写回表格：{detail}")
        self.outcome.writeback_ok = True

    @staticmethod
    def _writeback_hint() -> str:
        """没写回时的补救办法（读运行日志把成功的行重新写一遍）。"""
        return "temu-bot table writeback --live"

    def needs_writeback(self) -> bool:
        """这一次到底有没有东西要写回（干跑、取消过 → 没有）。"""
        return self.live and not self._cancelled_early

    # ------------------------------------------------------------ 串起来
    def _cancelled(self) -> bool:
        """用户请求取消了吗？——**只在还没动写操作时才认**。

        一旦复制/保存开始就不能半途停：断在 COPY 之后、APPLY 之前会留下一个
        "复制了但没改标题"的商品，而且 product_id 没落账 → 重跑就是重复铺货。
        所以写操作一旦开始，这一行必须跑完。
        """
        if self.should_cancel is None or self._wrote:
            return False
        try:
            return bool(self.should_cancel())
        except Exception:  # noqa: BLE001
            log.warning("取消回调出错（按不取消处理）", exc_info=True)
            return False

    def _run_steps(self) -> None:
        for step in (
            self.step_precheck,
            self.step_media,
            self.step_shop_check,
            self.step_find_template,
            self.step_copy,
        ):
            if self._cancelled():
                # 到这里还没动任何写操作 → 这一行什么都没发生，表格也不该被改
                self._cancelled_early = True
                self._add(
                    "CANCEL",
                    Status.SKIP,
                    "用户取消：在步骤之间停下（此时还没执行任何写操作）",
                )
                return
            if not step():
                return

        product_id = self.step_find_new()
        if product_id:
            # 先换图再保存：保存下去的才是这一行的图，而不是模板的图
            if not self.step_upload_images():
                return
            self.step_apply_fields(product_id)
        elif not self.live:
            # 干跑：没真复制出商品，也要把"会传哪几张图"列出来，
            # 不然用户看不到最要紧的那一步
            self.step_upload_images()
            self.step_apply_preview()

    def run(self) -> ListingOutcome:
        try:
            self._run_steps()
        except Exception:
            log.exception("第 %s 行跑挂了", self.row.row_index)
            raise
        finally:
            # 回写放在 finally：**失败的行也要写状态**，不然表格一直空着、
            # 下次还当"新建"跑。写回自己会吞掉"表被占用"这类错，不往外抛。
            try:
                self.step_writeback()
            except Exception:  # noqa: BLE001
                log.exception("回写表格时出错（上架结果不受影响）")
                self._add("WRITEBACK", Status.FAIL, "回写表格时出错，详见日志")
                self.outcome.writeback_ok = False
            if self._client is not None:
                self._client.close()
        return self.outcome
