"""本地网页控制台：http://127.0.0.1 上的一个页面，只服务本机。

模块分工：
    logs.py      日志环形缓冲（网页实时日志的来源）
    configio.py  配置读写（只写 config.local.yaml）与只读目录浏览
    jobs.py      后台任务运行器（跑上架主流程）
    app.py       FastAPI 装配与路由
    static/      单文件原生前端（无构建、无 jinja2）
"""

from temu_bot.web.app import create_app, serve
from temu_bot.web.jobs import BusyError, JobRunner, JobState, RunRequest
from temu_bot.web.logs import LogBuffer, LogEntry, RingBuffer

__all__ = [
    "BusyError",
    "JobRunner",
    "JobState",
    "LogBuffer",
    "LogEntry",
    "RingBuffer",
    "RunRequest",
    "create_app",
    "serve",
]
