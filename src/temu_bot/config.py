"""配置加载：config.yaml + config.local.yaml 深度合并 → pydantic 模型；密钥只走环境变量。

设计要点：
- 本项目根目录由 __file__ 推导（src/temu_bot/config.py → parents[2]）。
- 任何密码/密钥都不写进 yaml，只写环境变量名（api_key_env），由 ai_key() 在运行时读。
- config.local.yaml 存在时覆盖主配置（gitignore 掉），用于本机私密覆盖。
"""

from __future__ import annotations

import copy
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

# ---------------------------------------------------------------- 模型


@dataclass(slots=True)
class LoginInfo:
    """一个可用的**登录身份**（= 一个店小秘账号的登录态）。

    `shop_hint` 只是配置里顺手写的一句话，**不是判定依据**——店铺会变。
    真正算数的永远是：登录账号里实际有哪些店（`userIn.shopMap`）。
    """

    login: str
    profile_dir: Path
    session_file: Path = Path()
    has_profile: bool = False
    has_session: bool = False
    known: bool = False          # 配置里登记过
    shop_hint: str = ""          # 配置里写的店铺名（可能过期）

    @property
    def ready(self) -> bool:
        """能不能直接用（有会话文件，接口就能调）。"""
        return self.has_session


class ProjectCfg(BaseModel):
    table: Path
    sheet_name: str = "Sheet1"


class ToolboxCfg(BaseModel):
    app_dir: Path
    core: str = "Core.ps1"
    ledger: Path
    powershell: str = "powershell.exe"
    node: str = "node"

    @property
    def core_path(self) -> Path:
        return self.app_dir / self.core


# 工具箱的 ID 台账。SKU 重命名发出去的 11 位 ID **永远不能重发**，
# 所以台账并进项目自己管；桌面那个工具箱的 used_ids.txt 只在**首次**合并进来
# （取并集，只增不删），见 `Config.toolbox_seed_ledgers`。
TOOLBOX_LEDGER_NAME = "used_ids.txt"


class SheetCfg(BaseModel):
    header_row: int = 1
    multi_value_separator: str = "\n"
    separator_fallback_pattern: str = r"[\n\r,，;；|]+"
    backup_keep: int = 20
    statuses: dict[str, str] = Field(
        default_factory=lambda: {
            "success": "成功",
            "processing": "处理中",
            "failed": "失败",
            "need_human": "需人工",
        }
    )


class ViewportCfg(BaseModel):
    width: int = 1440
    height: int = 900


class BrowserCfg(BaseModel):
    provider: Literal["playwright_local", "system_chrome", "zhanfu"] = "playwright_local"
    # channel="chrome" 用系统安装的 Google Chrome；=null 用 Playwright 自带 Chromium
    channel: str | None = None
    # 直接指定 chrome.exe 路径（channel 认不出来时用，比如绿色版/非标准安装）
    executable_path: str | None = None
    headless: bool = False
    slow_mo_ms: int = 0
    viewport: ViewportCfg = Field(default_factory=ViewportCfg)
    args: list[str] = Field(default_factory=list)


class ThrottleCfg(BaseModel):
    action_min_s: float = 1.2
    action_max_s: float = 3.0
    every_n: int = 5
    rest_min_s: float = 60.0
    rest_max_s: float = 120.0
    max_qps: float = 1.0

    def scaled(self, speed: Literal["cautious", "normal", "fast"]) -> "ThrottleCfg":
        """speed 档位整体缩放等待时间：cautious 1.5x / normal 1x / fast 0.6x。"""
        factor = {"cautious": 1.5, "normal": 1.0, "fast": 0.6}[speed]
        data = self.model_dump()
        for key in ("action_min_s", "action_max_s", "rest_min_s", "rest_max_s"):
            data[key] = round(data[key] * factor, 2)
        data["max_qps"] = round(data["max_qps"] / factor, 3)
        return ThrottleCfg(**data)


class ShopCfg(BaseModel):
    # name = 表格「店铺名」列里出现的值（业务上的店名）
    name: str
    # login = 登录身份 key：profiles/<login> + session_<login>.json。
    # 不同店在不同店小秘账号/授权下，就配不同的 login，各自独立登录态。
    login: str | None = None
    # 该登录态下**应该**能看到的店小秘店铺名（核对一致性用；留空则用 name 比）
    expected_dxm_shops: list[str] = Field(default_factory=list)
    template_spuid: str | None = None
    profile_dir: Path | None = None
    cdp_port: int | None = None
    throttle: ThrottleCfg = Field(default_factory=ThrottleCfg)

    @property
    def profile_key(self) -> str:
        """登录态与浏览器 profile 用哪个 key（登录身份优先）。"""
        return (self.login or self.name).strip()

    @property
    def expected_shops(self) -> list[str]:
        return [s for s in (self.expected_dxm_shops or [self.name]) if s]


class AiProviderCfg(BaseModel):
    id: str
    type: str = "openai_compatible"
    base_url: str
    model: str
    api_key_env: str
    supports_vision: bool = True
    timeout_s: float = 60.0


class AiPromptsCfg(BaseModel):
    title_cn: str = "title_cn.j2"
    title_en: str = "title_en.j2"


class AiCfg(BaseModel):
    chain: list[str] = Field(default_factory=list)
    title_max_len: int = 120
    prefix_map: dict[str, str] = Field(default_factory=dict)
    suffix_map: dict[str, str] = Field(default_factory=dict)
    prompts: AiPromptsCfg = Field(default_factory=AiPromptsCfg)
    providers: list[AiProviderCfg] = Field(default_factory=list)


class RuntimeCfg(BaseModel):
    dry_run: Literal["plan", "form"] = "form"
    live: bool = False
    speed: Literal["cautious", "normal", "fast"] = "cautious"
    human_timeout_s: int = 900
    log_level: str = "INFO"
    screenshot_on_error: bool = True


class Config(BaseModel):
    project: ProjectCfg
    toolbox: ToolboxCfg
    sheet: SheetCfg = Field(default_factory=SheetCfg)
    browser: BrowserCfg = Field(default_factory=BrowserCfg)
    shops: list[ShopCfg] = Field(default_factory=list)
    ai: AiCfg = Field(default_factory=AiCfg)
    runtime: RuntimeCfg = Field(default_factory=RuntimeCfg)

    # 派生路径（不写进 yaml）
    root: Path = Field(default_factory=lambda: project_root())
    data_dir: Path = Field(default_factory=lambda: project_root() / "data")
    work_dir: Path = Field(default_factory=lambda: project_root() / "work")

    # ------------------------------------------------------------ 工具箱台账
    @property
    def toolbox_ledger(self) -> Path:
        """SKU 重命名发过的 11 位 ID 记在这（并进项目之后不再依赖桌面那个文件夹）。"""
        return self.data_dir / "state" / TOOLBOX_LEDGER_NAME

    @property
    def toolbox_seed_ledgers(self) -> list[Path]:
        """要合并进来的旧台账。桌面工具箱那份就是靠这个带过来的（存在才读）。"""
        return [self.toolbox.ledger]

    # ------------------------------------------------------------ 查询辅助
    def shop(self, name: str) -> ShopCfg | None:
        for s in self.shops:
            if s.name == name:
                return s
        return None

    def shop_or_default(self, name: str) -> ShopCfg:
        # --shop 传的是**登录身份**（如 hat-tshirt），配置里的 name 是表格店铺名；
        # 两者都要能查到，否则登录身份永远匹配不上配置。
        found = self.shop_by_login(name) or self.shop(name)
        if found is not None:
            if found.profile_dir is None:
                return found.model_copy(
                    update={"profile_dir": self.root / "profiles" / _safe_key(found.profile_key)}
                )
            return found
        # 未在配置里登记的店铺：用名字派生的 profile 目录，避免直接崩掉
        return ShopCfg(
            name=name,
            profile_dir=self.root / "profiles" / _safe_key(name),
        )

    def shop_by_login(self, login: str) -> ShopCfg | None:
        """按登录身份 key 找店铺配置（login 或 name 命中都算）。**只是提示**，别拿它做判定。"""
        target = login.strip().lower()
        for shop in self.shops:
            if shop.profile_key.strip().lower() == target or shop.name.strip().lower() == target:
                return shop
        return None

    def logins(self) -> list["LoginInfo"]:
        """**有哪些可用的登录身份**——一个身份 = 一个店小秘账号的登录态。

        用户 2026-10-16 明确：**店铺不固定、登录的店铺随时会改**，所以
        **不能拿配置里那份静态店铺表当唯一来源**。这里认两个来源：

          1. `data/state/session_<身份>.json` —— **登录时才会生成**，有它就是能用的身份；
          2. 配置里的 `shops` —— 就算还没登录也列出来，好让页面显示"待登录"。

        **不去扫 `profiles/` 目录**：那里面除了真正的登录态，还有一堆跑脚本留下的
        浏览器缓存目录（探查/自检留下的），扫进来只会把下拉框塞满没用的东西。
        真登录过的身份必然有会话文件，不会漏。
        """
        found: dict[str, LoginInfo] = {}

        def ensure(login: str) -> LoginInfo | None:
            login = (login or "").strip()
            if not login:
                return None
            key = login.lower()
            if key not in found:
                found[key] = LoginInfo(
                    login=login,
                    profile_dir=self.root / "profiles" / _safe_key(login),
                )
            return found[key]

        # 1) 会话文件（登录过 → 接口能直接用）
        #
        # 注意排除 `session_<身份>_localstorage.json`——那是 `session_<身份>.json`
        # 的**附属文件**（登录时另存一份 localStorage 备查，见 `session.py`），
        # 不是另一个身份。不排除的话会多出 `xxx_localstorage` 这种假身份。
        state_dir = self.data_dir / "state"
        if state_dir.is_dir():
            for entry in sorted(state_dir.glob("session_*.json")):
                if entry.stem.endswith("_localstorage"):
                    continue
                info = ensure(entry.stem[len("session_"):])
                if info is not None:
                    info.session_file = entry

        # 2) 配置里登记过的身份（可能还没登录）
        for shop in self.shops:
            info = ensure(shop.profile_key)
            if info is None:
                continue
            info.known = True
            info.shop_hint = shop.name
            declared = shop.profile_dir
            if declared is not None:
                info.profile_dir = Path(declared)

        for info in found.values():
            info.has_profile = info.profile_dir.is_dir() and any(info.profile_dir.iterdir())
            # 用 is_file()：默认值是 Path('.')，它 exists() 为真，会把"没登录"误判成"已登录"
            info.has_session = info.session_file.is_file()

        # 能用的排前面；同档按名字
        return sorted(found.values(), key=lambda i: (not i.has_session, not i.known, i.login))

    def provider(self, provider_id: str) -> AiProviderCfg | None:
        for p in self.ai.providers:
            if p.id == provider_id:
                return p
        return None

    def ai_key(self, provider: AiProviderCfg) -> str | None:
        val = os.environ.get(provider.api_key_env, "").strip()
        return val or None

    def throttle_for(self, shop_name: str) -> ThrottleCfg:
        return self.shop_or_default(shop_name).throttle.scaled(self.runtime.speed)

    # ------------------------------------------------------------ 目录
    def ensure_dirs(self) -> None:
        for p in (
            self.data_dir,
            self.data_dir / "logs",
            self.data_dir / "backups",
            self.data_dir / "reports",
            self.data_dir / "state",
            self.work_dir,
            self.root / "profiles",
        ):
            p.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- 加载


class ConfigError(RuntimeError):
    pass


def project_root() -> Path:
    """项目根目录。

    - 环境变量 `TEMU_BOT_ROOT` 优先。**测试靠它把 data/、work/ 也挪进沙箱**——
      只设 `TEMU_BOT_CONFIG` 是不够的：那只换配置文件，`data/`（备份、日志、state）
      仍会落到真实项目里，测试就会往真目录里写东西。
    - 源码运行：`src/temu_bot/config.py` → `D:\\temu_bot`（parents[2]）
    - 打包成 exe 后：**exe 所在目录**。这样 `config/`、`data/`、`work/`、`profiles/`
      都落在 exe 旁边，配置和登录态不会被封进包里、也好改。
    """
    override = os.environ.get("TEMU_BOT_ROOT")
    if override:
        return Path(override).resolve()
    if getattr(sys, "frozen", False):          # PyInstaller 等打包后为 True
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _safe_key(text: str) -> str:
    """把登录身份/店名变成安全的目录名。"""
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in text).strip("_")
    return safe or "default"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, val in (override or {}).items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = copy.deepcopy(val)
    return out


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"配置文件格式不对（顶层必须是映射）: {path}")
    return data


def config_paths(root: Path | None = None) -> tuple[Path, Path]:
    root = root or project_root()
    override = os.environ.get("TEMU_BOT_CONFIG")
    main = Path(override) if override else root / "config" / "config.yaml"
    local = main.with_name(main.stem + ".local" + main.suffix)
    return main, local


def load_config(path: Path | None = None, *, require: bool = True) -> Config:
    root = project_root()
    main, local = config_paths(root)
    if path is not None:
        main = Path(path)
        local = main.with_name(main.stem + ".local" + main.suffix)

    if require and not main.exists():
        raise ConfigError(f"找不到主配置: {main}")

    merged = _read_yaml(main)
    merged = _deep_merge(merged, _read_yaml(local))

    cfg = Config(**merged)
    cfg.root = root
    cfg.data_dir = root / "data"
    cfg.work_dir = root / "work"
    return cfg


def mask_secret(value: str | None) -> str:
    """日志/打印用：只露首尾，避免密钥泄漏。"""
    if not value:
        return "(未设置)"
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}***{value[-4:]}"


# ---------------------------------------------------------------- 对外别名
# Web 控制台要读写配置层，用公开名字，别去碰下划线私有函数。
read_yaml = _read_yaml
deep_merge = _deep_merge
