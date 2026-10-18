# STORY.md — 求职助手 Agent 平台 · 面试讲解

## ① 用户意图对齐

- **目标受众**：技术面试官（AI 应用工程师 / 后端方向），现场投屏讲解，5~15 分钟项目介绍环节，可能随时被打断深挖。
- **核心目标**：讲完后面试官相信——该项目是真做、真测、真有数据的 AI Agent 平台；候选人具备"先建基准再优化、接受被数据否决"的工程判断力与全链路降级治理能力。
- **PPT 长度**：14 页（Hero 页 3 个，占比 21%）。
- **视觉调性**：极简蓝白 / 清爽克制 / 数据驱动 / 工程专业。
- **内容边界**：必讲——六大亮点（Agent 循环、混合检索与重排、SSE 延迟治理、LLM 工厂降级链、幻觉治理、测试体系）与全部实测数据（5.8×、TTFF 3.7s、RRF 否决、155 项测试）；不讲——前端细节、部署运维细节、免费额度探测工具；禁碰——不夸大未实测指标，不编造数据。

## ② 页面布局骨架

- **页面总数与分章**：14 页 3 章。第 01 章「项目概览」= P3 扉页 + P4 定位全景 + P5 架构技术栈；第 02 章「核心亮点与实测数据」= P6 扉页 + P7 数据总览 + P8 Agent 循环 + P9 检索重排 + P10 SSE 延迟 + P11 LLM 工厂；第 03 章「工程质量与总结」= P12 扉页 + P13 输出治理与测试 + P14 结束页。
- **目录↔章节扉页契约**：目录声明 3 章 → 全篇有且仅有 3 个 section 扉页：目录第 01 章「项目概览」→ P3；第 02 章「核心亮点与实测数据」→ P6；第 03 章「工程质量与总结」→ P12。编号 01..03 连续，标题与目录逐字一致。
- **Hero 页定位**：P1 封面（全幅图骑线）、P7 数据总览（巨型数字 5.8×）、P14 结束页（居中金句）。3/14 = 21%，互不相邻（P1→P7 间隔 5 页，P7→P14 间隔 6 页）。
- **rhythm 曲线**：P1 peak → P2 valley → P3 transition → P4 valley → P5 valley → P6 transition → P7 peak → P8 valley → P9 peak → P10 valley → P11 valley → P12 transition → P13 valley → P14 peak。无连续 ≥3 valley（P4/P5 后接 transition，P10/P11 后接 transition）。
- **非对称版式**（7/14 = 50%，≥40% 达标）：P2 左标题+右内容、P4 非对称双栏、P5 左大图+右侧文字、P7 巨型数字+洞察、P8 非对称双栏、P9 左大图+右侧文字、P11 左标题+右内容。`左大图+右侧文字` 与 `非对称双栏` 合计 4 页 ≤ 40%（5.6）达标。
- **对称版式**（5 页，均为章节扉页/图表/结束等结构页）：P3、P6、P12 全屏视觉+大标题（章节扉页专用），P10 图表+洞察，P14 居中金句。相邻页版式均不相同。

## ③ 页面大纲

### P1 cover
- title: 求职助手 Agent 平台
- type: cover / role: hero / rhythm: peak
- layout: 全幅图+骑线文字
- visual: L1: hero_cover.png（全幅底图，文字自左下骑入，占图面 35%）
- visual_role: anchor
- density: 字数约35 / 图片 1 张 / 留白约40%
- anti_pattern: 禁止装饰小图塞标题区；禁止居中对称平铺排版
- description: 主标「求职助手 Agent 平台」+ 副标「项目讲解 · 技术面试」+ 辅助信息「AI 应用工程师方向 · 2026.10」。一句话定调：一个真测真数据的 AI Agent 工程。

### P2 catalog
- title: 目录
- type: catalog / role: supporting / rhythm: valley
- layout: 左标题+右内容
- visual: L3: 页脚徽标；条目编号用大号数字（≥40px 锚点）
- visual_role: evidence
- density: 字数约120 / 图片 0 张 / 留白约25%
- anti_pattern: 禁止条目只有两字标题；每条目须带一句 20 字以上导读
- description: 三章导航：01 项目概览（是什么、怎么架构）；02 核心亮点与实测数据（六大亮点、四组实测数字）；03 工程质量与总结（输出治理、测试体系、工程判断）。

### P3 section
- title: 01 项目概览
- type: section / role: transition / rhythm: transition
- layout: 全屏视觉+大标题
- visual: L1: section_overview.png（右侧 45% 主视觉，左侧章节大字）
- visual_role: anchor
- density: 字数约30 / 图片 1 张 / 留白约45%
- anti_pattern: 禁止四卡片章节预览；禁止铺满正文段落
- description: 章节大字「01 项目概览」+ 一句小标「一个求职场景的垂直 Agent：从 JD 解析到模拟面试的全链路」。

### P4 content
- title: 项目定位与功能全景
- type: content / role: supporting / rhythm: valley
- layout: 非对称双栏（62:38）
- visual: L1: 功能结构 SVG 树（左栏，占 B 区约 45%）；右栏数据点：9 工具 / 5 阶段交付 / 155 项测试
- visual_role: anchor
- density: 字数约220 / 图片 1 张 / 留白约25%
- anti_pattern: 禁止 50:50 等分双栏；禁止功能罗列成无层次的流水句
- description: 个人独占的求职垂直 Agent 平台：JD 解析、简历评分、模拟面试、会话级 RAG 问答；技术定位是「AI 应用工程为主、后端工程为辅」。判断：九个可插拔工具说明复杂度在 LLM 编排与检索治理，不在 CRUD。

### P5 content
- title: 系统架构与技术栈
- type: content / role: supporting / rhythm: valley
- layout: 左大图+右侧文字
- visual: L1: 架构分层 SVG（左 55%：前端 Vue3 → API 层 → 服务层（Agent 循环/检索/治理）→ 持久层 → 外部 LLM/FAISS）
- visual_role: anchor
- density: 字数约200 / 图片 1 张 / 留白约25%
- anti_pattern: 禁止把架构图缩成角落示意；禁止左右等分
- description: FastAPI + SQLAlchemy 2.0 + MySQL + LangChain + FAISS/BM25，前端 Vue3；四层分离，服务层是核心。判断：架构为「可插拔、可降级、可观测」服务，检索与治理组件均可独立替换。

### P6 section
- title: 02 核心亮点与实测数据
- type: section / role: transition / rhythm: transition
- layout: 全屏视觉+大标题
- visual: L1: section_data.png（右侧 45% 主视觉）
- visual_role: anchor
- density: 字数约30 / 图片 1 张 / 留白约45%
- anti_pattern: 禁止四卡片章节预览；禁止提前剧透结论段落
- description: 章节大字「02 核心亮点与实测数据」+ 小标「每组结论都来自自建基准，而非感觉」。

### P7 content
- title: 一页看全：四组实测数据
- type: content / role: hero / rhythm: peak
- layout: 巨型数字+洞察
- visual: 大数字：5.8×（≥110px 锚点）+ 三组副数据（3.7s / 0.14~0.33 / 155 项，≥48px）
- visual_role: anchor
- density: 字数约140 / 图片 0 张 / 留白约40%
- anti_pattern: 禁止数据装进等宽卡片横排；禁止只有数字没有判断
- description: 重排 21.4s→3.7s（5.8×，Top-4 重合 0.71）——性能优化以基准为证；TTFF 15~30s→3.7s——用户感知等待净减约 15 秒；RRF 与 LLM 排序重合率仅 0.14~0.33——方案被自己的数据否决；pytest 155 项全离线——质量可信。判断：先建基准、再动手优化、接受被否决，是这个项目的工程方法论。

### P8 content
- title: Agent 工具循环与容错
- type: content / role: supporting / rhythm: valley
- layout: 非对称双栏（65:35）
- visual: L1: ReAct 消息序列 SVG（左 65%：System→History→Human→AIMessage(tool_calls)→ToolMessage×N→最终回答 循环图）
- visual_role: anchor
- density: 字数约230 / 图片 1 张 / 留白约25%
- anti_pattern: 禁止把消息序列画成纯文字列表；禁止左右等分
- description: 自研生成器版 ReAct 循环（九工具、硬上限六轮），不用框架黑盒——为的是能插手流式进度与降级。工具错误以字符串回灌保证 tool_call 与 ToolMessage 一一对应，消息序列永不断裂；轮次耗尽返回中间内容并标记 degraded。判断：消息协议完整性优先于"抛异常"，错误本身也是给模型的信息。

### P9 content
- title: 混合检索与 listwise 重排
- type: content / role: supporting / rhythm: peak
- layout: 左大图+右侧文字
- visual: L1: 三级检索链路 SVG（左 55%：会话私有索引→公共索引→内置知识库，向量+BM25 双路合流→listwise 精排）
- visual_role: anchor
- density: 字数约230 / 图片 1 张 / 留白约25%
- anti_pattern: 禁止链路图画成单行线性列表；禁止把 5.8× 缩成正文小字
- description: FAISS 向量 + BM25 关键词双路召回、去重合并后精排，三级降级保证可用。重排从逐篇串行 N 次 LLM 调用改为 listwise 单次输出序号：21.4s→3.7s（5.8×），Top-4 重合率 0.71。RRF 方案被自建基准否决（重合率仅 0.14~0.33），根因是 BM25 按字切分导致排名本身是坏的。判断：性能优化先建基准，方案要能被数据否决——这比优化成功更能证明判断力。

### P10 content
- title: SSE 流式与延迟治理
- type: content / role: supporting / rhythm: valley
- layout: 图表+洞察
- visual: Chart(柱状图)：TTFF 优化前后对比（15~30s → 3.7s），旁配三项动作标注
- visual_role: evidence
- density: 字数约200 / 图片 1 张 / 留白约28%
- anti_pattern: 禁止只放图表不给结论；禁止把事件序列写成段落
- description: 耗时结构分析定位首因：单条消息内十余次串行 LLM 调用。三项动作——重排改 listwise 单次、幻觉校验移出关键路径异步补发、历史摘要水位线增量持久化——首字节降至 3.7s。SSE 事件顺序（meta→delta→tool_status→sources→done→verification）写入专项单测，done 提前让用户感知等待净减约 15 秒。判断：先做耗时结构分析，不凭感觉优化；每项改动都能说出省了哪一段。

### P11 content
- title: LLM 工厂与降级链
- type: content / role: supporting / rhythm: valley
- layout: 左标题+右内容
- visual: L1: 降级链路 SVG（右 60%：额度错误→沿链切换→粘性保持；流式首分片缓冲分支）
- visual_role: anchor
- density: 字数约220 / 图片 1 张 / 留白约25%
- anti_pattern: 禁止把配置项堆成表格；禁止左栏超过 35% 宽
- description: 13 处散装客户端构造 + SDK 默认重试（10 次×300s）最坏等 50 分钟；收口为统一工厂 lru_cache 单例，超时分层、重试压到 2 次。额度错误沿降级链粘性切换，流式仅在首分片前安全降级。判断：工厂收口解决的不是性能问题是配置漂移——外部依赖的构造必须有唯一入口。

### P12 section
- title: 03 工程质量与总结
- type: section / role: transition / rhythm: transition
- layout: 全屏视觉+大标题
- visual: L1: section_quality.png（右侧 45% 主视觉）
- visual_role: anchor
- density: 字数约30 / 图片 1 张 / 留白约45%
- anti_pattern: 禁止四卡片章节预览；禁止重复前章数字
- description: 章节大字「03 工程质量与总结」+ 小标「把模型的不确定性，变成可测试、可降级、可解释的行为」。

### P13 content
- title: 输出治理与测试体系
- type: content / role: supporting / rhythm: valley
- layout: 上下分栏（上 55 : 下 45，非等分）
- visual: L1: 五级 JSON 解析管道 SVG（上栏）+ 下栏测试策略三要点
- visual_role: evidence
- density: 字数约240 / 图片 1 张 / 留白约22%
- anti_pattern: 禁止上下 50:50 等分；禁止把 normalize 规则写成十行小字
- description: 幻觉后置验证：原子断言逐条归因，编造量化数据自动升级风险；「未验证」一律报 unknown 绝不冒充低风险，评分缺失置 None 绝不伪造 0 分。五级 JSON 解析管道 + 逐字段 normalize 把模型错乱转化为 warnings。pytest 155 项全离线：mock SDK 驱动降级路径、确定性假 embedding、事件顺序契约单测。判断：测试的主要价值不是"功能能跑"，而是"故障时行为正确"。

### P14 ending
- title: 总结
- type: ending / role: hero / rhythm: peak
- layout: 居中金句/巨型数字
- visual: 大数字装饰：5.8× / 3.7s / 155（底部小字带）；主金句 ≥ 44px
- visual_role: anchor
- density: 字数约60 / 图片 0 张 / 留白约45%
- anti_pattern: 禁止总结页堆砌全部前文数据；禁止超过两行半的金句
- description: 金句：「先建基准、接受被数据否决——用工程化治理 AI 的不确定性。」底部一行数据回顾：重排 5.8× · 首字节 3.7s · 155 项离线测试 · 9 工具全链路降级。
