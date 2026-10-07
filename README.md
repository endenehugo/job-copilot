# Job Copilot（求职助手 Agent 平台）

面向求职场景的垂直 Agent 应用：JD 解析、简历评分、项目经历优化、模拟面试、内置面试知识库，以及基于简历/JD 的会话级 RAG 问答。

- 后端：FastAPI + SQLAlchemy 2.0 + MySQL + LangChain（qwen-plus / qwen-vl-plus / text-embedding-v3 + FAISS + BM25 混合检索）
- 前端：Vue 3 + Vite + Pinia + Element Plus（开发中）
- 文档：接口文档由 FastAPI 自动生成，见 `/docs`

> 项目正在从 Flask 单体原型（llmrag）向前后端分离架构迁移，核心服务层沿用原型中经过线上排障验证的实现。

## 后端快速启动

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp ../.env.example ../.env                          # 填入 DASHSCOPE_API_KEY 与 MySQL 配置
python scripts/init_db.py                           # 建库（表在首次连接时自动创建）
uvicorn app.main:app --reload --port 8000
```

启动后：

- 健康检查：`GET http://127.0.0.1:8000/api/v1/system/health`
- Key 检测：`GET http://127.0.0.1:8000/api/v1/system/keycheck`
- 接口文档：`http://127.0.0.1:8000/docs`

## 前端快速启动

```bash
cd frontend
npm install
npm run dev   # http://localhost:5174（开发代理已指向 127.0.0.1:8018 后端）
```

生产构建：`npm run build`，产物在 `dist/`，由 Nginx 托管并反代 `/api` 与 `/conversation/image`（见 deploy/）。

## 目录结构

```text
backend/
├── app/
│   ├── main.py          # FastAPI 入口（lifespan、CORS、异常处理）
│   ├── core/            # 配置/统一响应/异常/依赖注入/JSON 容错解析
│   ├── api/             # APIRouter（system / conversations / documents / jobs ...）
│   ├── schemas/         # Pydantic 请求/响应模型
│   ├── services/        # 业务服务（检索、评分、面试、压缩、校验等）
│   ├── repositories/    # SQLAlchemy 持久层
│   ├── tools/           # Agent 工具（7 个）
│   └── utils/
├── scripts/init_db.py
├── tests/
└── resources/           # 源数据与 FAISS 公共索引（运行时产物不入库）
```

## 开发计划与进度

见 `docs/` 下的开发计划文档。当前进度：P4（Vue3 前端）已完成，P5（流式/部署/收尾）进行中。
