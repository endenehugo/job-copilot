# 面试预测：AI 应用后台 / RAG 方向

> 输入：`tools/fixtures/resume-ai-app-3y.txt`｜`tools/fixtures/jd-tencent-wecom-ai-backend.txt`
> 模式：Predict（只预测，不进入模拟面试）
> 依据：`references/question-and-scoring-contract.md`（评分契约格式）

## 1. Claim 提取

| ID | 类型 | 简历原话 | 为什么需要验证 |
|---|---|---|---|
| C1 | Metric | 问答准确率 90%+ | 无 baseline、无样本量、无口径，最容易被追穿 |
| C2 | Ownership | 主导 RAG 全流程开发 | 「全流程」是否含解析器/索引自研 |
| C3 | Technical | 语义切片 + 文心一言 Embedding + Milvus 检索 | 切片参数与索引类型必问 |
| C4 | Architecture | 限流降级、Redis 缓存、RabbitMQ 异步 | 只有名词，没有数据流与故障处理 |
| C5 | Result | 百万级文档秒级检索、响应 ≤2s | 规模与延迟的对应关系 |
| C6 | Metric | 1000+ 并发、可用性 99.9% | 压测模型与统计周期 |
| C7 | Gap | 全文无 C++ / 无 Linux 年限 | JD 硬门槛，必被质疑一次 |

## 2. 能力—证据—缺口矩阵

| JD 能力 | 简历证据 | 缺口 |
|---|---|---|
| RAG 流程研发 | C2/C3/C5 | 调优过程与评测方法 |
| 模型调用与调度框架 | C4 | 缺「统一调用层/超时/重试/降级链」的架构描述 |
| 高并发后台稳定性 | C6 | 缺压测、容量、故障演练 |
| 分布式容灾 | 无 | 真实缺口 |
| C++ / Linux | 无 | 硬门槛缺口 |

## 3. 高概率题（含评分契约）

### Q1（必问，风险最高）
**「简历写问答准确率 90%+，这个数字是怎么来的？」**

```yaml
question_id: rag-metric-01
claim_id: C1
intent: 验证指标口径、评测方法与个人归因
required_evidence:
  - 评测集来源（真实工单 / 人工构造）、样本量
  - 准确/正确的判定标准（人工标注？命中引用？）
  - baseline 是多少，提升来自哪个环节
  - 该指标是否线上持续监控
followup_triggers:
  - 只说"测了一下""感觉变好了"
  - 说不出 baseline 或分母
  - 把团队评测算成个人成果
stop_condition: 必要证据齐全 → 追问一个反事实（换模型/bad case 归因）后结束
```

追问链：
1. 300 条评测集怎么抽的？谁标注的？
2. baseline 71% 是改造前还是换模型前？
3. 剩下 10% 的错误，属于检索没召回、还是召回但生成答偏？各占多少？
4. 如果检索召回率提高但准确率不涨，你怎么定位？

### Q2（必问）
**「语义切片为什么是 500 字？重叠多少？标题层级怎么保留？」**

```yaml
question_id: rag-technical-01
claim_id: C3
intent: 验证技术决策与真实实现
required_evidence: 切片策略、重叠比例、边界处理（表格/代码块/跨页）、失败案例
followup_triggers: 只背通用最佳实践，说不出项目里的具体参数来源
stop_condition: 说明参数来源 + 一次调整前后的效果
```

### Q3（必问，Milvus）
**「百万级文档用什么索引？检索延迟怎么保证 2 秒内？」**

```yaml
question_id: rag-arch-01
claim_id: C5
intent: 验证规模认知与工程细节
required_evidence: 索引类型（IVF/HNSW）、nlist/ef 参数、集合切分、预加载、TopK 与重排耗时拆分
followup_triggers: 说不出「2 秒里检索占多少、生成占多少」
stop_condition: 拆分出耗时结构 + 一个瓶颈
```

### Q4（高概率，架构题）
**「你说的限流降级，具体降的是什么？模型挂了用户看到什么？」**

```yaml
question_id: llm-arch-01
claim_id: C4
intent: 验证调度/降级是真实设计还是名词堆砌
required_evidence: 降级对象（模型/功能/缓存兜底）、触发条件、恢复策略、用户侧表现
followup_triggers: 只有"加了限流"，没有阈值与触发条件
stop_condition: 给出一次真实的降级触发案例
```

### Q5（压力题）
**「JD 要求精通 C++、3 年 Linux 后台。你简历里没有 C++，怎么补？」**

```yaml
question_id: gap-cpp-01
claim_id: C7
intent: 验证诚实度与学习路径，而非技术本身
required_evidence: 承认缺口 + 可迁移能力（内存/并发/系统调用的实际接触）+ 具体学习计划
followup_triggers: 编造经历；或反过来贬低岗位技术栈
stop_condition: 边界清楚且给出可验证的下一步
```

### Q6（加分题）
**「ToB 场景下，多租户的知识库怎么做隔离？」**

```yaml
question_id: tob-01
claim_id: C5
intent: 验证是否真做过企业级交付（简历写了 3 个部门接入）
required_evidence: 数据隔离层级（库/集合/分区）、权限、配额
followup_triggers: 只会说"加了 tenant_id"
stop_condition: 说清隔离粒度与代价
```

## 4. 压力题与补充题

- 压力题：**「这个系统如果让你重做一遍，哪一部分你会推翻？」**——考察反思与真实参与度。
- 补充题：**「Token 成本下降 40% 是怎么算的？换模型、缓存、还是 Prompt 压缩？」**
- 补充题：**「意图识别 95%+ 的测试集和线上分布一致吗？」**

## 5. 优先准备顺序

1. Q1 指标口径（不准备必翻车，且影响所有数字的可信度）
2. Q4 调度与降级（JD 核心职责，简历最薄）
3. Q3 Milvus 规模细节（技术深度的分水岭）
4. Q5 C++ 缺口（诚实回答即可，不要恋战）
5. Q2 / Q6（加分项）

---

## 冒烟测试自检

- 读取：两份 fixture；`references/question-and-scoring-contract.md`（评分契约格式）。
- 产出：6 道核心题（每题带评分契约）+ 3 道补充/压力题 + 一条 4 层追问链；未生成「标准答案」，符合 skill 的 Predict 约定。
- 未执行：Grill（模拟面试）按指示跳过；未创建会话账本（用户未要求保存复盘到本地以外的位置）。
- 观察：skill 的「不给标准答案」「证据状态而非精确分数」两条约束在产出中可验证地生效。
