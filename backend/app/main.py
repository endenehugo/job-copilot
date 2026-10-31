"""Job Copilot 后端入口。

启动方式（在 backend/ 目录下）：
    uvicorn app.main:app --reload --port 8000
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import admin, auth, conversations, documents, exports, interviews, jobs, knowledge, models, resume_generation, resumes, skills, system
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.perf_trace import configure_perf_logging
from app.core.rate_limit import RateLimitMiddleware
from app.core.response import ok
from app.core.visitor_middleware import VisitorMiddleware
from app.utils import ResourceUtils
from app.utils.api_key_checker import check_all


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI):
    ResourceUtils.init(settings.resources_root)
    # 请求级分段计时（PERF {json}）要靠这个处理器才能进 journal。
    # 不接线的话 perf_trace 只在内存里记，线上永远看不到任何一行。
    configure_perf_logging()
    # B16/A22：启动时检测一次 API Key，结果缓存在 app.state 供 /health 查询。
    # "服务健康但 Key 无效"不再无感——部署后看日志或 /health 的 keys_ok 即可。
    try:
        report = await asyncio.to_thread(check_all)
        application.state.keys_ok = report.all_passed
        if not report.all_passed:
            failed = [r.name for r in report.results if not r.passed]
            logger.warning("API Key 启动检测未通过：%s；聊天/评分等功能将不可用", ", ".join(failed))
    except Exception as exc:  # 检测本身异常不能阻断启动
        application.state.keys_ok = None
        logger.warning("API Key 启动检测执行失败：%s", exc)
    # 管理员引导：admin_users 表为空且配置了 ADMIN_* 时自动建初始管理员
    from app.api.auth import ensure_initial_admin
    try:
        ensure_initial_admin()
    except Exception as exc:
        logger.warning("管理员引导执行失败（不影响启动）：%s", exc)
    yield


app = FastAPI(
    title="Job Copilot API",
    description="求职助手 Agent 平台：JD 解析、简历评分、项目优化、模拟面试与会话级 RAG 问答。",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS 白名单：生产是同源部署（Nginx 托管前端 + 反代 /api），浏览器同源请求
# 根本不经过 CORS，白名单留空即最小暴露面；开发期放行本地前端端口。
# 之前 allow_origins=["*"] 的写法等于"任何网页都能让浏览器跨站调你的 API"。
if settings.cors_allow_origins:
    cors_origins = settings.cors_allow_origins
elif settings.is_dev:
    cors_origins = ["http://localhost:5174", "http://127.0.0.1:5174"]
else:
    cors_origins = []
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
# 访客身份：懒签发签名 Cookie，写 request.state.owner_key。
# 放在 CORS 之后注册 = 在 CORS 内层，保证预检请求先被 CORS 处理掉
app.add_middleware(VisitorMiddleware)
# 限流放在最外层：配额/身份校验都还没跑就该把突发流量挡掉。
# 自己解 cookie 取身份，不依赖 VisitorMiddleware 的执行顺序。
app.add_middleware(RateLimitMiddleware)

register_exception_handlers(app)
app.include_router(system.router)
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(models.router)
app.include_router(skills.router)
app.include_router(conversations.router)
app.include_router(conversations.image_router)
app.include_router(documents.router)
app.include_router(jobs.router)
app.include_router(interviews.router)
app.include_router(resumes.router)
app.include_router(resume_generation.router)
app.include_router(exports.router)
app.include_router(knowledge.router)


@app.get("/", tags=["system"])
def index() -> dict:
    return ok({"name": "Job Copilot API", "docs": "/docs"})
