from __future__ import annotations

import logging
from dataclasses import dataclass

from langchain_community.embeddings import dashscope
from langchain_community.vectorstores import FAISS

from app.utils import ResourceUtils

logger = logging.getLogger(__name__)

# ============================================================
# 内置知识库数据
# 结构：列表，每项包含 title、category、content
# ============================================================

BUILTIN_KNOWLEDGE = [
    # ---- Python 后端面试题 ----
    {
        "title": "Flask 请求生命周期",
        "category": "python_backend",
        "content": """Flask 请求生命周期：
1. 客户端发起 HTTP 请求到达 WSGI 服务器（如 Gunicorn）
2. WSGI 服务器调用 Flask 应用实例的 wsgi_app 方法
3. 请求上下文（Request Context）被压入栈：request、session
4. 请求经过所有已注册的 before_request 钩子
5. URL 匹配：根据请求方法和路径查找对应的视图函数（route）
6. 视图函数执行并生成响应
7. 响应经过 after_request 钩子
8. 返回 HTTP 响应给客户端
9. 请求上下文出栈并销毁""",
    },
    {
        "title": "Flask Blueprint 与模块化",
        "category": "python_backend",
        "content": """Flask Blueprint 模块化：
Blueprint 是 Flask 实现模块化组织的核心机制。通过 Blueprint，可以将不同功能的路由、错误处理和静态文件组织到独立模块中。
优点：解耦业务、支持路由前缀、支持独立模板目录和静态文件目录。
在大型应用中，通常会按业务域拆分 Blueprint（如 user_bp、admin_bp、api_bp），然后在工厂函数中统一注册。""",
    },
    {
        "title": "SQLAlchemy 会话管理",
        "category": "python_backend",
        "content": """SQLAlchemy 会话（Session）管理：
Session 是数据库操作的工作单元，管理对象的生命周期。
关键点：
- Session 是事务性的，所有变更在 commit 时一次性写入
- Session 是轻量级的，应每个请求创建新 Session
- 常见模式：session = SessionLocal() / try-finally session.close()
- 使用 scoped_session 实现线程安全的会话工厂
懒加载（Lazy Loading）：关联对象在首次访问时才从数据库加载
N+1 问题：循环访问关联对象导致多次查询，使用 joinedload/subqueryload 优化""",
    },
    {
        "title": "Python 装饰器与闭包",
        "category": "python_backend",
        "content": """Python 装饰器（Decorator）：
装饰器是一个接受函数作为参数并返回新函数的可调用对象（通常是函数或类）。
用途：日志记录、权限校验、性能计时、缓存、重试机制。
核心原理：
1. Python 函数是一等公民，可以作为参数传递
2. 闭包：内部函数可以访问外部函数的变量（即使外部函数已返回）
3. @decorator 语法糖等价于 func = decorator(func)
functools.wraps 用于保留原函数的元信息（__name__、__doc__等）
常见例子：@app.route()、@login_required、@cache""",
    },
    {
        "title": "Gunicorn 与 WSGI",
        "category": "python_backend",
        "content": """Gunicorn（Green Unicorn）WSGI 服务器：
Gunicorn 是 Unix 平台的 WSGI HTTP 服务器，使用 pre-fork 模型。
工作模式：
- 主进程（Master）管理 Worker 进程
- Worker 类型：sync（同步）、gevent（协程）、uvicorn（ASGI）
- 常见配置：workers = 2 * CPU核心数 + 1
关键参数：--workers、--worker-class、--bind、--timeout、--keep-alive
WSGI 规范：定义了 Web 服务器与 Python Web 应用之间的接口标准
应用中通过 gunicorn.conf.py 或命令行参数配置""",
    },
    {
        "title": "RESTful API 设计规范",
        "category": "python_backend",
        "content": """RESTful API 设计原则：
1. 资源导向：URL 表示资源，而非操作（/users 而非 /getUsers）
2. HTTP 方法语义：GET 查询、POST 创建、PUT 全量更新、PATCH 部分更新、DELETE 删除
3. 状态码：200 OK、201 Created、204 No Content、400 Bad Request、401 Unauthorized、403 Forbidden、404 Not Found、500 Internal Server Error
4. 统一响应格式：{code, message, data}
5. 分页：使用 limit/offset 或 cursor 参数
6. 版本控制：URL 前缀 /api/v1/ 或请求头
7. 错误消息应包含详细的错误说明""",
    },

    # ---- MySQL / Redis / Linux ----
    {
        "title": "MySQL 索引原理",
        "category": "database",
        "content": """MySQL 索引原理：
B+ Tree 索引：InnoDB 默认使用 B+ Tree 结构
- 非叶子节点只存储键值，不存储数据
- 叶子节点存储完整的数据行或主键
- 叶子节点之间有指针链接，支持范围查询
聚簇索引（Clustered Index）：数据行物理顺序与索引顺序一致，InnoDB 的主键索引是聚簇索引
二级索引（Secondary Index）/ 辅助索引：叶子节点存储主键值，需要通过回表（回主键索引）查询完整数据
联合索引最左前缀原则：查询条件必须从索引最左侧开始匹配
EXPLAIN 分析查询计划：type、key、rows、Extra 是核心关注字段""",
    },
    {
        "title": "MySQL 事务与隔离级别",
        "category": "database",
        "content": """MySQL InnoDB 事务与隔离级别：
ACID：原子性（Atomicity）、一致性（Consistency）、隔离性（Isolation）、持久性（Durability）
四种隔离级别（由低到高）：
1. READ UNCOMMITTED：脏读、不可重复读、幻读都可能
2. READ COMMITTED：不可重复读、幻读可能（多数数据库默认）
3. REPEATABLE READ：幻读可能（MySQL InnoDB 默认，通过 MVCC+间隙锁解决幻读）
4. SERIALIZABLE：全部解决，性能最差
MVCC（多版本并发控制）：通过 undo log 实现数据行的多版本，实现非阻塞读
间隙锁（Gap Lock）：锁定索引记录之间的间隙，防止幻读""",
    },
    {
        "title": "Redis 核心数据结构",
        "category": "database",
        "content": """Redis 核心数据结构：
1. String（字符串）：缓存、计数器、分布式锁、Session 共享
2. List（列表）：消息队列、最新消息列表
3. Set（集合）：标签、去重、共同关注（交集）
4. Hash（哈希）：存储对象字段
5. ZSet（有序集合）：排行榜、延时队列（score 为时间戳）
6. Bitmap（位图）：签到统计、布隆过滤器
7. HyperLogLog：基数统计
8. GEO：地理位置查询
过期策略：定期删除 + 惰性删除
淘汰策略：LRU、LFU、TTL、Random 等""",
    },
    {
        "title": "Redis 缓存常见问题",
        "category": "database",
        "content": """Redis 缓存常见问题：
1. 缓存穿透（Cache Penetration）：查询不存在的数据，缓存和数据库都查不到
   解决：布隆过滤器、缓存空值（短过期时间）
2. 缓存击穿（Cache Breakdown）：热点 Key 过期，大量请求同时打到数据库
   解决：互斥锁（SETNX）、逻辑过期、永不过期
3. 缓存雪崩（Cache Avalanche）：大量 Key 同时过期
   解决：过期时间加随机值、多级缓存、限流降级
4. 缓存一致性：
   先更新数据库再删除缓存（Cache Aside 模式）是常用方案
   延迟双删：更新 DB → 删缓存 → 等待 → 再次删缓存""",
    },
    {
        "title": "Linux 常用运维命令",
        "category": "linux",
        "content": """Linux 常用运维命令：
进程管理：ps aux、top、htop、kill、pkill
端口查看：netstat -tlnp、ss -tlnp、lsof -i:端口号
磁盘管理：df -h、du -sh *、fdisk -l、lsblk
日志查看：tail -f、less、grep、awk、sed
系统信息：uname -a、cat /proc/cpuinfo、free -h、uptime
权限管理：chmod、chown、useradd、usermod
网络工具：curl、wget、ping、traceroute、nc
Docker：docker ps、docker logs、docker exec -it、docker-compose""",
    },
    {
        "title": "Nginx 反向代理与负载均衡",
        "category": "linux",
        "content": """Nginx 配置要点：
反向代理配置：
location /api/ {
    proxy_pass http://backend_server:5000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}
负载均衡策略：
1. 轮询（默认）：按顺序分发
2. weight：按权重分配
3. ip_hash：基于客户端 IP 的会话保持
4. least_conn：最少连接数优先
静态文件服务：alias /root; expires 7d;
HTTPS 配置：ssl_certificate + ssl_certificate_key""",
    },

    # ---- Flask 工程化 ----
    {
        "title": "Flask 工厂模式与应用上下文",
        "category": "flask_engineering",
        "content": """Flask 工厂模式（Application Factory）：
通过函数 create_app() 创建 Flask 应用实例，替代全局 app 对象。
优势：
1. 支持多环境配置（dev/test/prod）
2. 支持单元测试时创建隔离的 app 实例
3. 避免循环导入
应用上下文（Application Context）：
- app = current_app 代理对象，在任何有应用上下文的代码中都可访问
- 通过 app.app_context() 手动创建上下文
- 在请求处理中自动推送应用上下文
常用模式：在工厂函数中注册 Blueprint、初始化扩展、加载配置""",
    },
    {
        "title": "Flask 依赖注入",
        "category": "flask_engineering",
        "content": """Flask 依赖注入：
使用 injector 库或 flask-injector 实现依赖注入。
核心概念：
1. Binder：将接口绑定到具体实现
2. Injector：管理对象的创建和生命周期
3. @inject：标记需要自动注入的依赖
4. @dataclass + @inject：常见的服务定义模式
优点：降低耦合、易于测试、集中管理依赖
典型结构：
@inject
@dataclass
class Service:
    repository: Repository
在测试时可以轻松 Mock 依赖：Service(repo=mock_repo)""",
    },
    {
        "title": "Python Logging 最佳实践",
        "category": "flask_engineering",
        "content": """Python Logging 最佳实践：
1. 使用 logger = logging.getLogger(__name__) 按模块获取 logger
2. 日志级别：DEBUG < INFO < WARNING < ERROR < CRITICAL
3. 配置方式：basicConfig、dictConfig、YAML/JSON 配置文件
4. 结构化日志：包含时间戳、模块名、行号、请求 ID
5. Flask 集成：通过 app.logger 获取应用级 logger
6. 日志轮转：RotatingFileHandler、TimedRotatingFileHandler
7. 敏感信息脱敏：密码、Token 等不应出现在日志中
典型配置：控制台输出（开发） + 文件输出（生产）""",
    },
    {
        "title": "Flask 错误处理与异常捕获",
        "category": "flask_engineering",
        "content": """Flask 错误处理：
1. @app.errorhandler(code) 注册错误处理器
2. abort() 主动触发 HTTP 错误
3. 统一响应格式：所有错误都返回 {code, message, data}
推荐模式：
- 自定义 AppException 基类
- 业务异常继承 AppException，包含错误码和消息
- 在 errorhandler 中捕获并统一处理
- 500 错误应记录完整 traceback 到日志
- 400 错误应返回详细的参数校验信息""",
    },
    {
        "title": "Flask 项目结构模式",
        "category": "flask_engineering",
        "content": """推荐 Flask 项目结构：
project/
├── app/
│   ├── __init__.py        # 工厂函数 create_app()
│   ├── handler/           # 请求处理层（Controller）
│   ├── services/          # 业务逻辑层
│   ├── repository/        # 数据访问层（ORM）
│   ├── router/            # 路由注册
│   ├── response/          # 统一响应格式
│   ├── tools/             # LangChain 工具
│   ├── utils/             # 工具类
│   ├── templates/         # Jinja2 模板
│   └── static/            # 静态资源
├── config/                # 环境配置
├── resources/             # 运行时资源
├── run.py                 # 开发入口
└── wsgi.py                # 生产入口
这种分层结构清晰分离关注点，便于维护和测试。""",
    },

    # ---- Agent / RAG 原理 ----
    {
        "title": "RAG 工作原理",
        "category": "agent_rag",
        "content": """RAG（Retrieval-Augmented Generation）工作原理：
RAG 通过检索外部知识库来增强大语言模型的生成能力，解决 LLM 的知识截止问题和幻觉问题。
核心流程：
1. 文档切分（Chunking）：将文档切分成适当大小的片段
2. 向量化（Embedding）：使用 Embedding 模型将文本转为向量
3. 向量存储：将向量存入向量数据库（FAISS、Pinecone、Weaviate 等）
4. 检索（Retrieval）：用户提问时，将问题也转为向量，通过相似度匹配检索相关片段
5. 增强（Augmentation）：将检索到的片段作为上下文与问题拼接到一起
6. 生成（Generation）：LLM 根据增强后的提示生成回答
关键指标：检索准确率、生成相关性、端到端延迟""",
    },
    {
        "title": "Agent 工作模式与工具调用",
        "category": "agent_rag",
        "content": """Agent（智能体）工作模式：
Agent 是能够感知环境、自主决策并执行行动的 AI 系统。
核心能力：
1. 工具调用（Tool Calling / Function Calling）：LLM 输出结构化工具调用指令
2. 记忆（Memory）：短期记忆（上下文窗口）+ 长期记忆（外部存储）
3. 规划（Planning）：将复杂任务分解为子任务（ReAct、Plan-and-Execute）
4. 反思（Reflection）：评估自身输出并迭代优化
工具调用流程：
1. 用户提问 → LLM 分析意图
2. LLM 决定使用某个工具并生成调用参数（JSON）
3. 系统执行工具并返回结果
4. LLM 结合工具结果生成最终回答
循环直到：达到最大迭代次数、LLM 决定直接回答、或用户终止""",
    },
    {
        "title": "Prompt Engineering 技巧",
        "category": "agent_rag",
        "content": """Prompt Engineering 核心技巧：
1. 系统提示（System Prompt）：定义角色、规则和输出格式
2. 少样本学习（Few-Shot）：给出输入-输出示例，引导模型行为
3. 思维链（Chain-of-Thought）：让模型逐步推理，提高复杂问题准确率
4. 结构化输出：约束 JSON Schema 或使用工具调用格式
5. 指令层次：最重要的指令放在最前面
6. 负面提示：明确告诉模型不要做什么
7. 温度控制：低温度（0.1-0.3）用于确定性任务，高温度（0.7-0.9）用于创意任务
8. 分而治之：将复杂任务拆解为多个简单的子任务""",
    },
    {
        "title": "LangChain 核心组件",
        "category": "agent_rag",
        "content": """LangChain 核心组件：
1. Models（模型）：LLM（ChatTongyi、ChatOpenAI）、Embeddings（DashScopeEmbeddings）
2. Prompts（提示）：PromptTemplate、ChatPromptTemplate、FewShotPromptTemplate
3. Chains（链）：LLMChain、ConversationChain、SimpleSequentialChain
4. Memory（记忆）：ConversationBufferMemory、ConversationSummaryMemory
5. Retrieval（检索）：VectorStoreRetriever、ContextualCompressionRetriever
6. Tools（工具）：BaseTool 自定义工具、Tool 快捷工具
7. Agents（智能体）：AgentExecutor、initialize_agent、tool_calling 模式
8. Callbacks（回调）：处理流式输出、日志记录、Token 计数
9. Output Parsers（输出解析器）：PydanticOutputParser、StrOutputParser""",
    },
    {
        "title": "Embedding 模型与向量检索",
        "category": "agent_rag",
        "content": """Embedding 模型与向量检索：
Embedding 是将文本映射到高维向量的技术，语义相近的文本在向量空间中距离更近。
DashScope text-embedding-v3：
- 维度：1024 维
- 支持多语言（中英文混合效果良好）
相似度计算：
- 余弦相似度（Cosine Similarity）：最常用
- 点积（Dot Product）：适用于归一化向量
- 欧氏距离（Euclidean Distance）：适用于低维向量
FAISS（Facebook AI Similarity Search）：
- 高效的向量相似度搜索库
- 支持 CPU 和 GPU 加速
- IndexFlatIP（暴力搜索，最准确）
- IndexIVFFlat（聚类索引，加速搜索）""",
    },
    {
        "title": "FAISS 索引构建与检索",
        "category": "agent_rag",
        "content": """FAISS 索引构建与检索实践：
构建索引步骤：
1. 文档切分（Chunking）：按固定大小或语义边界切分
2. 向量化（Embedding）：将每个 Chunk 转为向量
3. 索引构建：FAISS.from_texts() 或从向量构建
4. 持久化存储：index.save_local()
5. 索引加载：FAISS.load_local()
检索策略：
1. 相似度阈值检索（similarity_score_threshold）：只返回高于阈值的文档
2. top-k 相似度检索（similarity）：返回最相似的 k 个文档
3. MMR（Maximum Marginal Relevance）：平衡相关性和多样性
生产中应考虑：索引更新策略、多租户隔离、增量索引、并发检索""",
    },
]


@dataclass
class BuiltinKnowledgeService:
    _db: FAISS | None = None
    _embeddings = None

    def rebuild_index(self) -> dict:
        """从内置知识数据重建 FAISS 索引"""
        texts = []
        metadatas = []

        for item in BUILTIN_KNOWLEDGE:
            title = item.get("title", "")
            category = item.get("category", "")
            content = item.get("content", "").strip()
            if not content:
                continue
            full_text = f"标题：{title}\n分类：{category}\n\n{content}"
            texts.append(full_text)
            metadatas.append({
                "title": title,
                "category": category,
                "source": "builtin_knowledge_base",
            })

        if not texts:
            return {"chunk_count": 0}

        embeddings = self._ensure_embeddings()
        db = FAISS.from_texts(texts, embeddings, metadatas=metadatas)
        index_dir = self._get_index_dir()
        import os
        os.makedirs(index_dir, exist_ok=True)
        db.save_local(index_dir)
        self._db = db

        logger.info("Built-in knowledge index rebuilt: %d chunks", len(texts))
        return {"chunk_count": len(texts)}

    def retrieve(self, query: str, k: int = 3, category: str | None = None) -> list[dict]:
        """检索内置知识库"""
        db = self._load_db()
        if db is None:
            return []

        retriever = db.as_retriever(
            search_type="similarity_score_threshold",
            search_kwargs={"k": k * 2, "score_threshold": 0.3},
        )
        docs = retriever.get_relevant_documents(query)

        results = []
        for doc in docs:
            metadata = doc.metadata or {}
            if category and metadata.get("category") != category:
                continue
            results.append({
                "title": metadata.get("title", ""),
                "category": metadata.get("category", ""),
                "content": doc.page_content,
                "score": getattr(doc, "score", 0),
            })
            if len(results) >= k:
                break

        return results

    def get_context(self, query: str, k: int = 3) -> str:
        """获取知识库上下文文本（供 LLM 使用）"""
        docs = self.retrieve(query, k=k)
        if not docs:
            return ""
        return "\n\n---\n\n".join(
            f"【{d['title']}（{d['category']}）】\n{d['content']}"
            for d in docs
        )

    def list_categories(self) -> list[str]:
        """列出所有知识分类"""
        categories = set()
        for item in BUILTIN_KNOWLEDGE:
            cat = item.get("category", "")
            if cat:
                categories.add(cat)
        return sorted(categories)

    def _ensure_embeddings(self):
        if self._embeddings is None:
            self._embeddings = dashscope.DashScopeEmbeddings(model="text-embedding-v3")
        return self._embeddings

    def _load_db(self):
        if self._db is not None:
            return self._db

        index_dir = self._get_index_dir()
        import os
        if not os.path.isdir(index_dir):
            return None
        if not any(name.endswith(".faiss") for name in os.listdir(index_dir)):
            return None
        self._db = FAISS.load_local(
            index_dir,
            self._ensure_embeddings(),
            allow_dangerous_deserialization=True,
        )
        return self._db

    @staticmethod
    def _get_index_dir() -> str:
        return ResourceUtils.get_resource_path("faiss_index_knowledge")
