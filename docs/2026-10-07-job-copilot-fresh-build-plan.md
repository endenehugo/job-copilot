# Job Copilot 直接开发计划（新仓库版）

> 日期：2026-10-07。本计划替代《2026-10-07-fastapi-vue-refactor-plan.md》（原地重构路线已否决，旧文件保留作对照）。
> 总策略：**新仓库、新壳、核心服务原样搬运**。代码库分三层对待：核心服务层是资产，原样复制；Web 壳很薄，按 FastAPI 惯例新写；jQuery 前端与旧 demo 链路是负债，不搬。

---

## 一、项目定位与命名

- 新仓库名：`job-copilot`；项目名沿用「求职助手 Agent 平台」。
- 定位：面向求职场景的垂直 Agent 应用（JD 解析、简历评分、项目经历优化、模拟面试、内置知识库），前后端分离：FastAPI（`/api/v1`）+ Vue 3。
- 旧仓库 `llmrag` 冻结：开发期作为**活的参照物**（行为基线），阿里云上旧版继续运行，新版对齐后切换；`career_docs/`（简历、面试材料）**不迁入新仓库**，继续留在旧仓库。

### 三条纪律（防止失控成第二系统）

1. **先搬后改**：服务层原样 copy 进新仓库、先跑通，再做解耦（`current_app.config` → Settings 注入）；不边搬边"优化"逻辑。
2. **功能冻结**：新项目只对齐旧应用已有行为，过程中冒出的新想法一律记 backlog，只允许加一个 SSE 端点。
3. **时间盒**：总预算 13–17 个工作日；超支时先砍 P5 的 SSE，再砍 P3 旧债修复的 4–6 项。

---

## 二、搬运对照表（本计划的核心）

### A. 原样搬运（copy 进 `backend/app/`，先跑通再解耦）

| 来源（旧仓库） | 内容 |
|---|---|
| `services/document_parser_service.py` | 文档解析（utf8/gbk 兜底、pdf 逐页、docx2txt、失败 safe_delete） |
| `services/document_index_service.py` | 切分/索引/混合检索/重排（唯一需要解耦改造的服务） |
| `services/conversation_chat_service.py` | 对话编排：agent 循环、多模态、图片 URL 白名单 |
| `services/conversation_store_service.py` | 会话/消息/文档 CRUD、自动标题 |
| `services/context_compression_service.py` | 上下文压缩（20 条阈值、摘要失败截断降级） |
| `services/context_verification_service.py` | 回答校验 + 引用标注（逆序插入、风险分级） |
| `services/builtin_knowledge_service.py` | 内置知识库（23 条、5 类） |
| `services/job_description_service.py` 等 4 个 | JD 解析/简历评分/项目改写/模拟面试的 prompt 与 schema |
| `services/export_service.py`、`image_analysis_service.py` | Markdown 导出、截图识别 JD |
| `repository/*.py`（7 个 + mysql_base） | SQLAlchemy 2.0 持久层，仅换 session 提供方式 |
| `tools/*.py`（7 个） | Agent 工具（错误当字符串返回约定、args_schema） |
| `utils/api_key_checker.py`、`resouce_utils.py` | Key 检测、资源路径（路径收敛到 Settings） |
| `resources/` 源数据 | `电商产品数据.txt`、内置知识文本、公共 faiss_index |
| config 中的参数值 | chunk 700/120、阈值 0.35/0.4/0.3、上传白名单、20MB 上限 |

### B. 搬运时合并改造（已计入 P1 工作量）

1. 四份重复的"五级 JSON 解析"→ 收成 `core/json_utils.py` 一份（逻辑不改，只去重）。
2. 四处 `current_app.config.get` → 构造注入 Settings。
3. `image_handler.save_analysis` 与 `JobHandler._save_analysis` 两份重复 → job service 单一方法。
4. `setup_mysql.py` 精简为 `backend/scripts/init_db.py`（建库建用户；7 张表由 `create_all` 自动建）。
5. `gunicorn.conf.py` 参数（workers=1、threads=4 gthread、timeout=180）吸收进新部署配置。

### C. 不搬（新仓库天然不存在）

- 旧 demo 链路：`chat_single/memory/agent/rag` handler 与路由、`/test/test`。
- 旧前端全部：`index.html`、`conversation_index.js`、`index.js`、`firebase.js`、`credentials.js` 及旧静态资源。
- `config/config_{env}.py`、injector `module.py`、`response.py`（语义吸收进 `core/`）。
- `career_docs/`、`服务器配置.md`（含服务器信息，不进公开作品集仓库）。
- 旧 README（全新重写）。

### 必须原样保留的防御性代码（"不必要的代码"的边界）

这些看起来平庸，每条背后是一次真实排障，删掉就是把坑重新踩一遍：

1. 五级 JSON 修复管道（去重收拢，但不能删）。
2. 阈值 + 兜底两级检索、文件名锚点前缀（`文件名：xxx` 进 embedding 语料）。
3. `_extract_store_documents`（从 FAISS index.pkl 反解 BM25 语料）+ 分数放 `metadata["score"]` 的约定。
4. 解析失败 safe_delete、gbk 兜底、"可能是扫描件"提示。
5. 多模态历史的 markdown 占位符重建 + 图片缺失跳过。
6. DashScope 多模态响应结构拍平。
7. 工具"错误当字符串返回"约定 + Pydantic args_schema 校验。
8. 上传文件磁盘名用 conversation_id + uuid 生成，中文文件名永不进路径。
9. 引用角标逆序插入、幻觉风险三级分级。
10. 压缩摘要失败时截断降级。
11. workers=1 与 OpenMP 依赖隔离的原因注释。

---

## 三、技术选型

| 层 | 选型 | 说明 |
|---|---|---|
| 后端 | FastAPI + uvicorn（生产 gunicorn UvicornWorker） | 自动 OpenAPI、与现有 pydantic 2.x 兼容 |
| 配置 | pydantic-settings + .env | 替代 4 套 config 文件 |
| ORM | SQLAlchemy 2.0 | 原样，7 张表 schema 不变 |
| 同步策略 | 端点一律普通 `def`（线程池） | dashscope/FAISS 均同步阻塞，不做伪 async |
| 前端 | Vue 3 + Vite + Pinia + Element Plus + ECharts | |
| HTTP/渲染 | axios 拦截器 + markdown-it + DOMPurify | |
| 语言 | 前端 JS（后续可升 TS） | |

## 四、目录结构

```text
job-copilot/
├── backend/
│   ├── app/
│   │   ├── main.py            # lifespan（Key 检测）、CORS、静态挂载、路由注册
│   │   ├── core/              # config.py / response.py(泛型信封) / exceptions.py / deps.py / json_utils.py
│   │   ├── api/               # conversations / documents / jobs / resumes / interviews / exports / knowledge / images / system
│   │   ├── schemas/           # Pydantic 请求/响应模型
│   │   ├── services/          # ← 原样搬运（15 个）
│   │   ├── repositories/      # ← 原样搬运（7 个 + mysql_base）
│   │   ├── models/            # ORM 模型集中（可选清理）
│   │   ├── tools/             # ← 原样搬运（7 个）
│   │   └── utils/
│   ├── scripts/init_db.py     # 建库建用户
│   ├── resources/             # 源数据入库；uploads/、faiss_index_uploads/ 运行时 ignore
│   ├── tests/                 # pytest：迁 4 个单测 + API 合同测试
│   └── requirements.txt       # 全量锁版本
├── frontend/
│   ├── src/{api,stores,views,components/{sidebar,chat,panels},router}
│   ├── vite.config.js         # dev 代理 /api → 127.0.0.1:8000
│   └── Dockerfile             # node build → nginx 托管
├── deploy/                    # nginx.conf、docker-compose.yml（mysql + backend + frontend）
├── docs/                      # 架构说明、部署文档
├── .env.example
├── .gitignore
└── README.md                  # 全新撰写
```

## 五、接口契约

- 响应信封不变：`{code, message, data}`，axios 拦截器统一解包。
- 路径 `/api/v1` 前缀，29 条核心路由平移（对齐旧应用"同参同果"）+ 新增 1 条 SSE = 30 条。
- 下线旧 demo 接口（前端从未使用）：`/chat/single`、`/chat/memory`、`/chat/agent`、`/chat/rag`、`/test/test`；如需最小演示端点，可选保留 `/api/v1/chat/single`。
- Pydantic 把隐式契约显式化：`mode ∈ {agent, rag, memory}`、`image_urls` 可选数组、评分维度枚举等。
- `/docs` 自动生成的 Swagger 即接口文档交付物。

## 六、分阶段开发计划（合计 13–17 个工作日）

### P1 新仓库初始化 + 骨架 + 服务层搬运（2.5–3 天）

- `git init`、monorepo 骨架、.gitignore、.env.example、ruff 配置（可选）；从第一天起用 conventional commits（feat/fix/chore），一阶段一串干净提交——面试官会翻提交历史。
- `core/` 四件套 + `json_utils.py`；`main.py`（lifespan Key 检测、CORS 允许 5173、静态挂载图片目录）。
- **搬运第二节 A 表全部文件**，随后做 B 表五项合并改造。
- pytest 就位：迁入现有 4 个单测（document_index / image_multimodal / web_search / word_document）。
- 交付标准：pytest 绿；`/docs` 可见 health 与 keycheck；被搬运服务在 FastAPI 进程内可实例化。

### P2 核心链路 API：会话 / 文档 / 检索 / 对话（3–4 天）

- schemas：CreateConversationRequest、ChatRequest（mode 枚举、image_urls）、ConversationDetailResponse、DocumentOut 等。
- conversations（create/list/detail/chat/image upload/serve image）+ documents（upload/delete），上传大小与扩展名校验、失败清理语义对齐。
- repositories 的 session 从旧注入方式改为 Depends（保留 scoped_session 语义与 create_all）。
- 交付标准：冒烟清单 1–5 双端口对照通过（创建会话 → 上传 docx → 三模式问答含引用/来源/幻觉徽标 → 多模态 → 删文档重建索引）。

### P3 求职业务 API + 旧债修复（2–3 天）

- jobs / resumes / interviews / exports / knowledge 五组路由与 schemas。
- 顺带修复（新仓库里成本最低）：① `interview_sessions.total_score` Integer→Float；② 索引全量重建改 tmp 目录 + 原子 rename；③ `_invoke_agent` 的 `tool.invoke` 包 try/except 返回错误 ToolMessage；④ BGE reranker 懒加载缓存；⑤ MultiplyTool 参数校验。
- 决策项（建议做）：内置知识库接入 `get_context_with_details` 第三级兜底（约 10 行），让"三级数据源"从注释变成事实；不做则从 README 删掉该说法。
- 交付标准：冒烟清单 6–12 通过；旧债①–⑤各有一个回归单测。

### P4 Vue 前端（4–5 天）

1. 脚手架与布局（1 天）：Vite + Vue3 + Pinia + Element Plus + ECharts；axios 实例（信封解包、错误提示）；Workspace 双栏布局（侧栏 280px + 主区）。
2. 会话与聊天（1.5 天）：会话列表/新建/切换/折叠；MessageList（markdown 渲染、引用角标、SourcePanel、幻觉风险徽标）；图片上传缩略图预览；Enter/Shift+Enter。
3. 结构化面板（1 天）：JD 表单 + 截图上传；ScoreCard（总分 + 四维条 + ECharts 雷达图 + 优势/缺口/建议）；ProjectRewriteCard（一键复制）；面板折叠/关闭/导出。
4. 面试 + 知识库 + 文档（1–1.5 天）：InterviewPanel（开始/答题/逐题评价/总结/历史回放）、DocumentPanel（el-upload + 状态 + 删除）、简历版本选择器与对比、KnowledgePanel。
- 交付标准：以旧前端为对照逐项对齐用户可见功能；`npm run build` 产物在 Nginx 下可用。

### P5 流式、部署、收尾（2–3 天）

- SSE：`POST /api/v1/conversation/chat/stream`，事件 `meta → delta* → sources → verification → done`。策略：命中工具的请求整段返回，未命中的常见路径用 ChatTongyi streaming 逐 token 推送；失败自动降级非流式；前端 fetch ReadableStream 打字机效果。
- 部署：docker-compose（mysql8 + backend + nginx-frontend）；nginx.conf 托管 dist、反代 `/api`、保留长超时；另附阿里云 Ubuntu 的 uvicorn + systemd 手工路线（对应旧服务器环境）。
- README 全新撰写（以 job-copilot 名义：定位、架构图、快速启动、API 一览、已知边界）。
- 交付标准：六步演示脚本（上传简历 → 输入 JD → 雷达图评分 → 改写 → 模拟面试 → 导出）全流程跑通；新版部署上线，旧版下线。

### 时间汇总与裁剪顺序

| 阶段 | 预估 |
|---|---|
| P1 骨架 + 搬运 | 2.5–3 天 |
| P2 核心链路 | 3–4 天 |
| P3 求职业务 + 旧债 | 2–3 天 |
| P4 Vue 前端 | 4–5 天 |
| P5 流式/部署/收尾 | 2–3 天 |
| **合计** | **13–17 天** |

超支裁剪顺序：SSE → 旧债修复④⑤ → P4 第 4 步的部分面板 → 知识库接入决策项。最小可交付 = P1–P3 + P4 的 1–3 步。

## 七、新旧对照验证（冒烟清单，双端口）

旧应用 `127.0.0.1:5000`，新应用 `127.0.0.1:8000`，同请求对照响应结构（字段名、信封、枚举、空值形状）：

1. Key 检测与健康检查；2. 创建会话；3. 上传 docx（parsed→indexed、char_count>0）；4. agent 对话（answer、sources 名称+相关性、verification 风险分级、引用角标）；5. 多模态图片问答；6. JD 分析（job_role/keywords/requirements/bonus_points/total_score/四维分/strengths/gaps/suggestions）；7. 截图分析链路；8. 项目改写（original_issues/improved_version/python_backend_version/agent_version）；9. 面试 start/answer（questions、expected_points、evaluation/score/next_action/follow_up）；10. 面试回放 + 简历版本对比；11. 三类导出；12. 知识库 rebuild/query/categories/status。

## 八、仓库工程化（作品集质量）

- **commit 规范**：conventional commits，按阶段成串；不留 "实现第三阶段" 这类含糊信息。
- **.gitignore**：`__pycache__/`、`.env`、`node_modules/`、`dist/`、`backend/resources/uploads/`、`backend/resources/faiss_index_uploads/`、`parsed_docs/`、`generated_docs/`、`.idea/`。
- **resources 策略**：源数据（内置知识文本、示例业务数据）入库；运行时产物（会话索引、上传文件）ignore，提供 rebuild 脚本重建公共索引。
- **敏感信息**：密钥只走 .env；服务器 IP/域名不进 README；提供 .env.example。
- **LICENSE**：MIT（可选）。

## 九、测试策略

- 现有 4 个单测迁 pytest；P3 旧债①–⑤各配回归单测。
- API 合同测试：pytest + TestClient 对冒烟清单 12 步做结构断言（不验 LLM 内容）；LLM 用 FakeService + Depends override，离线可跑。
- 前端：仅把旧仓库的渲染断言改写为 vitest 纯函数测试，不做组件测试。

## 十、风险与对策

| 风险 | 对策 |
|---|---|
| 边搬边改导致引入新 bug | 纪律一：搬运提交与解耦提交分开，搬运提交保证 pytest 绿后再解耦 |
| 范围膨胀（重写 prompt、加功能） | 纪律二：功能冻结 + backlog；唯一新功能是 SSE |
| 同步调用混入 async | 端点一律 def；仅 SSE 端点 async，实现前审查阻塞调用 |
| 版本耦合（langchain/pydantic/fastapi） | P1 第一天先装一套验证 import，requirements 全量锁版本 |
| 前端对齐遗漏 | 以旧前端为对照逐项核对，不靠"感觉差不多" |
| 搬运漏掉防御性代码 | 第二节边界清单逐项打勾验收 |

## 十一、最终验收清单

1. 冒烟清单 12 步双端口对照一致；2. 六步演示脚本全流程跑通（含打字机效果）；3. pytest 全绿（含旧债回归）；4. `/docs` 可作接口文档交付；5. docker-compose 一键起本地环境、生产部署文档更新；6. README 全新且与实际一致；7. 旧仓库冻结归档，career_docs 留存。

## 十二、旧仓库收尾

- 新版上线后 `llmrag` 停止开发（保留全部历史与 career_docs）。
- 面试文档《面试深挖问题与参考答案》中所有"代码依据"的文件路径与行号指向旧仓库，job-copilot 结构稳定后需要整体更新一遍（行号会变，结论不变）。
- 简历表述从"重构"改为："从 0 到 1 设计并实现前后端分离的求职助手平台（FastAPI + Vue3）……沉淀自上一代 Flask 原型的全部核心能力与排障经验。"
