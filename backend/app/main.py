"""Job Copilot 后端入口。

启动方式（在 backend/ 目录下）：
    uvicorn app.main:app --reload --port 8000
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, conversations, documents, exports, interviews, jobs, knowledge, models, resumes, system
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.response import ok
from app.utils import ResourceUtils
from app.utils.api_key_checker import check_all

import logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI):
    ResourceUtils.init(settings.resources_root)
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

# 与旧版行为一致：开发期放开跨域；前端拦截器按信封 code 判错，不依赖 CORS 凭证
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)
app.include_router(system.router)
app.include_router(auth.router)
app.include_router(models.router)
app.include_router(conversations.router)
app.include_router(conversations.image_router)
app.include_router(documents.router)
app.include_router(jobs.router)
app.include_router(interviews.router)
app.include_router(resumes.router)
app.include_router(exports.router)
app.include_router(knowledge.router)


@app.get("/", tags=["system"])
def index() -> dict:
    return ok({"name": "Job Copilot API", "docs": "/docs"})
