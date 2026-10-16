# 一次性探查脚本（存档）

这些是摸接口/翻前端 JS 时写的一次性脚本，**不是程序的一部分**，`temu_bot` 不 import 它们。

留在这儿是因为以后要再探接口（比如 Temu 改版）时还能拿来改。分几类：

| 前缀 | 干什么 |
|---|---|
| `hunt_*` / `probe_*` | 猜接口路径、试参数（就是靠这些定下了 `getSign` 的 `bucket`/`region`） |
| `pull_*` / `chunk_imports` / `fetch_chunks` / `jsfind` / `js_endpoints` | 抓店小秘前端 chunk，从压缩过的 JS 里挖接口和调用点 |
| `find_*` | 在 chunk 里搜某个符号/字段是哪儿来的 |
| `check_*` / `show_*` / `peek_*` / `pick_*` / `dump_*` | 看一眼商品/表格/图片长什么样（只读） |
| `test_upload` / `test_cos_url` / `check_img_md5` | 上传链路的中间验证 |

**能用的、留在 `tools/` 根目录的**是这些：

| 脚本 | 干什么 |
|---|---|
| `live_run_rows.py` | 真跑若干行（一行失败就停；商品ID 由流程自己回写） |
| `reset_rows.py` | 清空若干行的回写列，让它们变回"新建"（**破坏性**，默认干跑，会存档旧 ID） |
| `verify_products.py` | 把跑过的商品读回来核对标题/图片 |
| `verify_showcase.py` | 核对"新主图第 1 + 模板展示图保留 + 原主图已删 + 颜色未动" |
| `dry_run_all.py` | 把表里每一行都干跑一遍 |
| `check_all_rows.py` / `check_images.py` / `audit_images.py` | 图片规格体检（只读） |
| `check_titles.py` | 英文标题合规体检（非 ASCII / 连续空格 / 超长） |
| `table_overview.py` / `show_row.py` / `peek_cells.py` / `inspect_table.py` | 看表格现状（只读） |
| `test_image_swap.py` | 在真实商品上验证「换图」（可逆，有 `--restore`） |
| `compare_tables.py` | 比两张商品信息表是不是逐格相同 |
| `check_deps.py` / `check_web_stack.py` | 依赖与控制台技术栈自检 |
| `mine_old_logs.py` | 挖旧工具的日志（GBK，要显式解码） |
| `test_dialog.py` | 单独试系统文件夹对话框 |
