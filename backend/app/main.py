"""Job Copilot 后端入口。

启动方式（在 backend/ 目录下）：
    uvicorn app.main:app --reload --port 8000
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import system
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.response import ok
from app.utils import ResourceUtils


@asynccontextmanager
async def lifespan(application: FastAPI):
    ResourceUtils.init(settings.resources_root)
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


@app.get("/", tags=["system"])
def index() -> dict:
    return ok({"name": "Job Copilot API", "docs": "/docs"})
