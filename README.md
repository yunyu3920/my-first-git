# temu-bot

自建 Temu 自动上架工具。**独立实现**，功能对齐你原来用的商业工具（Baihui AutoTool），
代码自己可控：不复用它的代码，不碰它的授权校验。

## 关键事实：上架走的是店小秘，不是 Temu 后台

原工具的日志（`C:\Users\A\Desktop\log\app_2026-*.log`，4 天样本）显示它**从不点 Temu 页面**，
全程用 httpx 直调 **店小秘** 的 web 接口；Temu 域名出现 0 次，`www.dianxiaomi.com` 几千次。

还原出来的接口面（次数 = 4 天累计）：

| 接口 | 作用 | 次数 |
|---|---|---|
| `GET /api/userIn.json`、`/api/userInfo.json` | 账号/店铺信息 | 5 |
| `POST /api/popTemuProduct/pageList.json` | 半托管商品列表（分页） | 85 |
| `GET /api/popTemuProduct/edit.json?id=<商品ID>` | 商品详情（模板基线） | 每个商品 2 次 |
| `POST /api/banjia/copyProduct.json` | **复制商品** | 677 |
| `GET /api/album/index.json` | 图片空间（余量/列表） | 2574 |
| `POST /api/cos/getSign.json` | 取对象存储上传签名 | 1403 |
| `PUT https://wxalbum-*.dianxiaomi.com/wxalbum/...` | **直传图片**（每个商品 2 张） | 1402 |
| `POST /api/cos/cosDxmCallBack.json` | 上传回调登记 | 1402 |
| `POST /api/popTemuProduct/add<hash>.json` | **保存商品** | 701 |

所以本项目的路线是：**Playwright 只负责人工登录一次 → 导出 cookie → 业务全走 httpx 接口**。
不写页面选择器，因此不怕店小秘改版。范围：Temu **半托管**（pop）。

### 用哪个浏览器

`config.yaml` 里 `browser.channel: chrome` —— **用本机安装的 Google Chrome**（不是 Playwright
自带的 Chromium）。实测对比（同一探测脚本跑两种配置，看系统里出现的浏览器进程）：

| 配置 | 出现的 chrome.exe |
|---|---|
| `channel: chrome` | 仅 `C:\Program Files\Google\Chrome\Application\chrome.exe` |
| 自带 Chromium | 上面那个 + `%LOCALAPPDATA%\ms-playwright\chromium-1248\...\chrome.exe` |

注意：**用你的 Chrome 程序 ≠ 复用你 Chrome 里的登录态**。程序仍使用独立配置目录
`D:\temu_bot\profiles\<店铺>\`，所以第一次仍要在这个窗口里登录一次店小秘（之后长期复用）。
好处是不碰你日常浏览器的数据；`doctor` / `browser selfcheck` 会显示实际用的是哪个浏览器与版本。

- 默认 **dry-run**：把请求都构造好但不发提交请求，产出对比报告给你核对
- 真实提交必须显式 `--live`（且要单独批准）
- 低速、可续跑；验证码/风控只暂停等人工

## 状态

| 阶段 | 内容 | 状态 |
|---|---|---|
| P0 | 骨架 / 配置 / 日志 / 表格读写层 / doctor | ✅ 完成 |
| P1 | 浏览器登录 + 登录态导出 + 接口客户端骨架 + 网络录制器 | ✅ 完成 |
| P2 | 接口契约（复制 / 传图 / 保存的请求体）；单行 dry-run | ✅ 完成 |
| P3 | 图片管线（尺寸规范 + **工具箱已原生并进**，见 `docs/工具箱.md`） | ✅ 完成（PSD 套图未做） |
| P4 | AI 标题生成/翻译 | 待开工 |
| P5 | 批量 / 断点续跑 / 本地网页控制台 / 采集工作区 | ✅ 完成（断点续跑未做） |

**现在能完整走通的一条线**：
收集图片 → 校验 → 干跑 → 真实上架（复制模板 + 只换主图 + 填表里的 SKU 货号 +
回写商品ID/状态）→ 图片批处理生成清单（11 位 ID - 导表 - 保留 11 位 ID）。
默认 **371 条测试**。

**日常只用双击 `0-启动 temu-bot.bat`** —— 它起一个只在本机跑的服务并自动开浏览器。

**怎么用看 → [`docs/使用手册.txt`](docs/使用手册.txt)**（纯文本，记事本就能打开；
打包时也会跟着发出去）。

## 打包 / 发布

想拷到别的电脑用（不用装 Python），或者推到 git 仓库、发新版，见
**[`docs/打包与发布.md`](docs/打包与发布.md)**：

```powershell
uv sync --group packaging
.\.venv\Scripts\python.exe tools\build_app.py     # 产出 dist\temu-bot\
```

## 快速开始（源码运行）

```powershell
cd D:\temu_bot
$py = .\.venv\Scripts\python.exe

# 环境自检
& $py -m temu_bot.cli doctor

# 人工登录一次（弹出浏览器 → 登录店小秘 → 把窗口关掉就算完事）
& $py -m temu_bot.cli login --shop hat-tshirt --until-closed
```

### 启动器清单（双击即用）

| 文件 | 作用 | 要你做什么 |
|---|---|---|
| **`0-启动 temu-bot.bat`** | **主入口**：起本机服务 + 自动开浏览器进控制台 | 双击，然后别关那个黑窗口 |
| `1-登录店小秘.bat` | 人工登录一次（登录态存 `data/state/session_<身份>.json`） | 在浏览器里登录店小秘，登完把窗口关掉 |
| `2-检查接口.bat` | 验证"浏览器登录 → 接口操作"通了 | 无 |
| `7-干跑上架一行.bat` | 干跑一行（不写任何数据） | 输入行号（登录身份留空 = 自动认） |
| `8-检查图片并出图.bat` | 按 Temu 规格出图（原图只读） | 无 |
| `0-上架前检查.bat` | 只读校验「信息表格 + 图片文件夹」 | 无 |
| `0-收集图片生成表格.bat` | 把素材文件夹整理成商品信息表 | 选文件夹、填店铺与模板 SPU |

> 早期录制/排障用的一次性启动器（`3-/4-/5-录制*.bat`、`9-*.bat`）已经挪到
> `tools/_archive/bats/` 归档，根目录只留日常要用的。
> 录制产物在 `data/reports/recording/<时间戳>.jsonl`（请求明细）+ `-bodies/`（响应体）。

其他常用命令：

```powershell
& $py -m temu_bot.cli session show --shop hat-tshirt             # 看登录态（只显示 cookie 名）
& $py -m temu_bot.cli table read -n 3 --files                  # 读商品表
& $py -m temu_bot.cli table headers                            # 表头漂移校验
& $py -m temu_bot.cli browser selfcheck                        # 只验证浏览器能起来
```

## 目录

```
config/config.yaml          主配置（店铺↔profile、限速、路径、AI 链）
config/selectors/temu.yaml  页面选择器（接口路线下基本用不到，留作兜底）
data/state/session_<店铺>.json  接口用的登录态（含 cookie，**不要外传/提交**）
data/state/run.db           运行状态（细粒度状态机的真相源）
data/logs/                  每天 .log（人读）+ .jsonl（结构化）
data/backups/               回写商品表前的自动备份（保留 20 份）
data/reports/recording/     网络录制结果：*.jsonl + <ts>-bodies/ 响应体
data/reports/probe/         页面快照：元素清单 *.json + *.png + *.html
profiles/<店铺>/             每店铺独立浏览器用户目录（登录态载体）
work/<row_key>/             该行原图快照(src) / 产出(out) / manifest
src/temu_bot/
  sheet/        表格列契约、读取、占用保护与安全回写
  browser/      Playwright 适配层、页面探测、**网络录制器**
  dianxiaomi/   店小秘接口客户端（httpx）+ 登录态管理
  doctor.py     环境自检
  cli.py        命令行入口
tools/          一次性小工具（inspect_table.py 免依赖读 xlsx；mine_old_logs.py 挖旧日志接口）
```

## 关键设计

- **登录态**：Playwright 人工登录一次 → `storage_state` 导出 cookie + 店小秘的 localStorage
  到 `data/state/session_<店铺>.json` → httpx 客户端带着它调接口。过期会明确报错让你重登。
- **表格只当报表**：细粒度状态在 `run.db`，回写 xlsx 的只有粗状态
  （`成功` / `处理中` / `失败` / `需人工`），不破坏你现有筛选习惯。
- **上架最后一步 `WRITEBACK`**：跑一行就把商品ID + 状态 + 问题项写回表里。
  这不是顺手记一笔，而是**防重复铺货的闸门**——表里没有商品ID 的行，下次跑还是"新建"。
  表被 Excel/WPS 占用时写不成，那时 `outcome.failed` **仍然保持 False**（商品已经建好了，
  说"失败"会诱使人重跑）；画面会点名哪几行没写回，关掉表后
  `temu-bot table writeback --live` 补上。
- **幂等键** `row_key = sha1(文件夹路径|主图|模板产品SPUID)`，不依赖行号，插行不错位。
- **查重安全网**：你表里第 32 行是「状态=失败但商品ID 已填」——说明商品可能已经建出来了。
  所以"提交过但没核验"的行，重跑前必须先按标题/货号查重，绝不重复铺货。
- **占用保护**：写表前查 `~$` 锁文件 + 独占打开试探；被 Excel/WPS 占用就报错中止，
  绝不带病写；落盘走「同目录临时文件 + os.replace」原子替换。
- **复用你的《图片批处理工具箱》**（`Core.ps1`）：11 位 ID 重命名、`used_ids.txt` 台账去重、
  GBK 清单导出、Node+sharp 格式转换都调它，不重写。中文路径走「临时 .ps1(UTF-8 BOM) + -File」。
- **录制器不落密钥**：cookie / authorization / token 类请求头的值一律不记录，只记名字。

## 已确认的业务规则

- **店铺由人工切换，工具负责核对**（用户明确要求）：上架时按表格第 10 列「店铺名」
  人工切换店铺/账号，工具**必须先核对当前登录的店与表里这行要上的店是否一致；
  不一致必须拦下来提醒，绝不继续铺货**。
  - 对应命令：`dxm check --shop <登录身份> --expect "<表格里的店铺名>"`
  - 代码：`DxmClient.verify_shop()` / `assert_shop()`（`assert_shop` 抛 `ShopMismatch`）
- **登录身份 ≠ 表格店铺名**：`--shop` 参数是**登录身份 key**（对应
  `profiles/<key>` + `data/state/session_<key>.json`）；`config.yaml` 的
  `shops[].name` 才是表格里的店铺名，用 `login` 关联到登录身份，
  `expected_dxm_shops` 用来核对。
  - 已知映射：表格 `A hat tshirt` → 登录身份 `hat-tshirt`（店小秘 shopId 9013981）
  - 表格 `A lovely costume` → 登录身份 `lovely`（**尚未登录**，它不在 hat-tshirt 那个账号里）
- **价格/库存**：复制模板商品时**继承模板值**，工具不干预。
- **颜色规格**：单色不做规格，每个商品只传主图 + 素材图。
  **颜色/尺码一律不改**（`parentSpecName` / `specName` / `productSkuSpecReqs` 原样保留）。
- **SKU 货号**：用表里第 9 列「一键生成SKU货号前缀编码」填每个 SKU 的 `extCode`，
  **不沿用模板的**；SKC 级的货号（`mainProductSkuSpecReqs[*].extCode`）保持模板的不动。
- **商品图**：只换主图——新主图顶到轮播第 1 位，**模板的其它展示图保留不动**。
- **失败文案**：`状态` 列写 `成功` / `处理中` / `失败` / `需人工`，原因写 `问题项` 列。
- **目标平台**：Temu **半托管**（接口族 `popTemuProduct`）。
- **搬家不扣米豆**：实测连续复制 6 个商品，`balanceView` 所有桶都没变。
  查额度（只看）：`dxm balance --shop <登录身份>`。
  **真正要防的是重复铺货**：主流程跑完会把商品ID 写回表（`WRITEBACK` 步）；
  表开着 Excel 时写不成，关掉表跑 `temu-bot table writeback --live` 补上。

## 店小秘命令速查

```powershell
$py = .\.venv\Scripts\python.exe

& $py -m temu_bot.cli dxm shops  --shop hat-tshirt                  # 当前登录能看到哪些店
& $py -m temu_bot.cli dxm check  --shop hat-tshirt --expect "A hat tshirt"   # 核对店铺一致性
& $py -m temu_bot.cli dxm balance --shop hat-tshirt                 # 米豆额度
& $py -m temu_bot.cli dxm list   --shop hat-tshirt -n 10 --state offline      # 列待发布商品
& $py -m temu_bot.cli dxm show   --shop hat-tshirt --id <商品id>     # 看商品完整结构
& $py -m temu_bot.cli dxm copy   --shop hat-tshirt --product <模板id> --target "A hat tshirt"   # 搬家（默认 dry-run，加 --yes 才真发）
& $py -m temu_bot.cli dxm publish --shop hat-tshirt --ids <id,id>    # 发布（默认 dry-run）
```

接口契约细节见 `docs/店小秘接口契约.md`。
