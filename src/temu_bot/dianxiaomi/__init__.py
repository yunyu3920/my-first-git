"""店小秘（www.dianxiaomi.com）接口客户端。

业务：复制模板商品 → 改标题 → 传主图/素材图 → 保存 → 回读商品ID。
登录态：由 Playwright 人工登录一次，导出 cookie/localStorage 到 data/state/session_<店铺>.json；
之后所有请求用 httpx 带着这份登录态直调店小秘的 web 接口（与原商业工具同样的做法）。
"""

from temu_bot.dianxiaomi.client import DxmClient
from temu_bot.dianxiaomi.session import ShopSession, load_session, save_session_from_context

__all__ = ["DxmClient", "ShopSession", "load_session", "save_session_from_context"]
