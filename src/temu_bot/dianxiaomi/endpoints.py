"""接口端点注册表：把「操作 → 候选路径」从代码里抽出来。

用户 2026-10-18 的要求是「店小秘改版后程序别报错、要能应变」。这个项目不点页面，
业务全走 `/api/*.json`，所以真正会变的是**接口路径**和**返回结构**。

这里管路径：

- 每个操作配**一串候选**，代码按顺序试；前一个 404/405（多半是改名了）就换下一个；
- 默认值内置在本文件；
- `config/dxm_endpoints.json` 可以覆盖（**改完重开程序就行，不用重新打包**）：

      {
        "album_delete": ["/api/album/delPic.json", "/api/cos/deletePic.json"],
        "copy": ["/api/banjia/copyProduct.json"]
      }

  值可以是「字符串」或「字符串数组」，也可以写成 `{"paths": [...]}`；
- 配置文件缺了 / 写坏了 → **静默回落内置默认**，不影响上架。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# 保存接口路径里那段构建期固定 token（从前端 service 层实的）
SAVE_TOKEN = "3f8c2a97"

# 操作名 → 候选路径（第一个是首选）
DEFAULT_ENDPOINTS: dict[str, tuple[str, ...]] = {
    "user_in": ("/api/userIn.json",),
    "user_info": ("/api/userInfo.json",),
    "product_list": ("/api/popTemuProduct/pageList.json",),
    "offline_counts": ("/api/popTemuProduct/getOfflineCounts.json",),
    "online_counts": ("/api/popTemuProduct/getOnlineCounts.json",),
    "product_edit": ("/api/popTemuProduct/edit.json",),
    "copy": ("/api/banjia/copyProduct.json",),
    "save": (f"/api/popTemuProduct/add{SAVE_TOKEN}.json",),
    # 半托管发布有两套命名空间，旧式见 pddkjProduct/...
    "publish": (
        "/api/product/popTemu/batchPublish.json",
        "/api/pddkjProduct/batchPublish3f8c2a97.json",
    ),
    "to_wait_publish": ("/api/product/popTemu/batchToWaitPublish.json",),
    "batch_delete": ("/api/product/popTemu/batchDelete.json",),
    "midou_balance": ("/api/midou/valueService/balanceView.json",),
    "album_index": ("/api/album/index.json",),
    "album_list": ("/api/album/list.json",),
    # 图片空间删除：新旧两个都见过，按顺序试
    "album_delete": ("/api/album/delPic.json", "/api/cos/deletePic.json"),
    "album_from_net": ("/api/album/toUploadPicFromNet.json",),
    "upload_sign": ("/api/cos/getSign.json",),
    "upload_callback": ("/api/cos/cosDxmCallBack.json",),
}


class EndpointRegistry:
    """按操作名取候选路径。配置文件只做覆盖，不做删减。"""

    def __init__(self, overrides: dict[str, Any] | None = None) -> None:
        self._ops: dict[str, list[str]] = {k: list(v) for k, v in DEFAULT_ENDPOINTS.items()}
        self.overridden: set[str] = set()
        self._apply(overrides)

    def _apply(self, overrides: dict[str, Any] | None) -> None:
        if not isinstance(overrides, dict):
            return
        for op, spec in overrides.items():
            paths = _paths_of(spec)
            if not paths:
                log.warning("接口配置里「%s」不是有效路径，忽略", op)
                continue
            self._ops[str(op)] = paths
            self.overridden.add(str(op))

    def paths(self, op: str) -> list[str]:
        return list(self._ops.get(op) or [])

    def first(self, op: str) -> str:
        paths = self.paths(op)
        if not paths:
            raise KeyError(f"没有为「{op}」配置任何端点")
        return paths[0]


def _paths_of(spec: Any) -> list[str]:
    if isinstance(spec, str):
        spec = [spec]
    elif isinstance(spec, dict):
        spec = spec.get("paths")
    if isinstance(spec, str):
        spec = [spec]
    if not isinstance(spec, (list, tuple)):
        return []
    out: list[str] = []
    for item in spec:
        if isinstance(item, str) and item.strip().startswith("/"):
            out.append(item.strip())
    return out


def endpoints_path(cfg: Any) -> Path:
    return Path(cfg.root) / "config" / "dxm_endpoints.json"


def load_registry(cfg: Any) -> EndpointRegistry:
    """读配置覆盖层。**读不了就用内置默认**，绝不因为配置坏了上不了架。"""
    path = endpoints_path(cfg)
    if not path.is_file():
        return EndpointRegistry()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        log.warning("接口配置文件读不了（%s），改用内置默认：%s", path, exc)
        return EndpointRegistry()

    registry = EndpointRegistry(data)
    if registry.overridden:
        log.info("接口配置覆盖了 %d 个操作：%s", len(registry.overridden), "、".join(sorted(registry.overridden)))
    return registry
