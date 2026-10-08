# Job Copilot（求职助手 Agent 平台）

面向求职场景的垂直 Agent 应用：JD 解析、简历评分、项目经历优化、模拟面试、内置面试知识库，以及基于简历/JD 的会话级 RAG 问答。

- 后端：FastAPI + SQLAlchemy 2.0 + MySQL + LangChain（qwen-plus / qwen-vl-plus / text-embedding-v3 + FAISS + BM25 混合检索）
- 前端：Vue 3 + Vite + Pinia + Element Plus（开发中）
- 文档：接口文档由 FastAPI 自动生成，见 `/docs`

> 项目正在从 Flask 单体原型（llmrag）向前后端分离架构迁移，核心服务层沿用原型中经过线上排障验证的实现。

## 快速启动

Windows：**双击 `start.bat`**（自动起后端+前端并打开浏览器；`stop.bat` 停止）。
Git Bash / Linux：`./start.sh`（停止：`./stop.sh`）。
脚本顶部可配置 Python 路径与端口；首次运行会自动安装前端依赖。

## 后端快速启动

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp ../.env.example ../.env                          # 填入 DASHSCOPE_API_KEY 与 MySQL 配置
python scripts/init_db.py                           # 建库（表在首次连接时自动创建）
uvicorn app.main:app --reload --port 8018           # 端口与 start.sh / vite 代理保持一致
```

启动后：

- 健康检查：`GET http://127.0.0.1:8018/api/v1/system/health`
- Key 检测：`GET http://127.0.0.1:8018/api/v1/system/keycheck`
- 接口文档：`http://127.0.0.1:8018/docs`

## 前端快速启动

```bash
cd frontend
npm install
npm run dev   # http://localhost:5174（开发代理已指向 127.0.0.1:8018 后端）
```

生产构建：`npm run build`，产物在 `dist/`，由 Nginx 托管并反代 `/api` 与 `/conversation/image`（见 deploy/）。

## 聊天流式输出

`POST /api/v1/conversation/chat/stream`（SSE）：事件序列 `meta → delta* → sources → verification → done`。
未命中工具的请求逐 token 推送；命中工具时工具轮非流式、最终回答整段下发；
校验与引用标注依赖完整回答，在 done 前一次性返回。前端 fetch ReadableStream 渲染打字机效果。

## 部署

完整方案见 `deploy/部署指南.md`（阿里云 Ubuntu · systemd + Nginx · 含数据迁移、回滚与排障）。两条路线选其一：
- **宿主机手工**（当前推荐）：`deploy/push.ps1 -Server root@<公网IP> -FirstTime` 上传并初始化 → 写 `.env` → 建库 → `deploy/import-data.sh` 迁数据 → `deploy/push.ps1` 正式发布。
- **Docker**：`cp .env.example .env`（补齐密钥、`MYSQL_ROOT_PASSWORD`、`MYSQL_PASSWORD`）→ `docker compose --env-file .env -f deploy/docker-compose.yml up -d --build`，一键拉起 mysql + backend + nginx(frontend)。

部署要点：Nginx 对 `/api/v1/conversation/chat/stream` 必须 `proxy_buffering off`；后端保持 `workers=1`（进程内缓存语义）；生产依赖已最小化，无 torch/OpenMP 冲突；站点默认同时监听 80 与 8080（裸 IP 走 80 实测可用，受限的是未备案域名）。

## 安全与密钥

- **密钥只放 `.env`**（`DASHSCOPE_API_KEY` / `METASO_API_KEY` / `MYSQL_PASSWORD`），`.env` 与 `.env.*` 已被 `.gitignore` 忽略，仓库里只保留 `.env.example` 与 `deploy/.env.production.example` 两个占位模板。
- 生产环境下 `.env` 权限为 `640 root:jobcopilot`，由 systemd `EnvironmentFile` 注入，不随代码发布（`push.ps1` 与 `release.sh` 都显式排除）。
- `GET /api/v1/system/keycheck` 的脱敏只回显固定前缀与长度（`sk-w****（共 N 位）`），不返回尾部字符——尾部可用于校验猜测的 Key。
- **提交前自检**（提交钩子会自动跑，也可手动）：

  ```bash
  git config core.hooksPath .githooks      # 启用 pre-commit 钩子（每台克隆一次）
  bash deploy/check-secrets.sh --tracked   # 扫已跟踪文件
  bash deploy/check-secrets.sh --history   # 推 GitHub 前扫全部历史
  ```

  命中时只报文件名与模式名，**不回显匹配原文**，避免密钥进终端日志。
- 站点本身没有账号体系，公网访问请用 Nginx Basic Auth 门禁：`sudo bash deploy/enable-basic-auth.sh`（详见 `deploy/部署指南.md` 第八章）。

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

见 `docs/` 下的开发计划文档。当前进度：P5（流式/部署/收尾）已完成，全部五个阶段交付。
