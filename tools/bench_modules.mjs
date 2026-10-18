// 模块耗时基准：按真实调用链逐个打接口，记录"发起 → 结果返回"的墙钟时间。
// 用法：node tools/bench_modules.mjs [--base=http://127.0.0.1:8018/api/v1] [--shot=/path/jd.png]
//
// 为什么要接口级而不是点 UI：UI 多一层渲染与节流，测出来的数会把前端开销算进 LLM 延迟里。
// 每个 LLM 调用都是一次真实请求，耗时会受模型负载与配额降级影响，跑一次不代表稳定分布。
import { readFileSync, writeFileSync } from 'node:fs'
import { basename } from 'node:path'

const args = Object.fromEntries(
  process.argv.slice(2).map((a) => {
    const [k, ...v] = a.replace(/^--/, '').split('=')
    return [k, v.join('=')]
  })
)
const BASE = args.base || process.env.JC_BASE || 'http://127.0.0.1:8018/api/v1'
const SHOT = args.shot || ''
const TIMEOUT = 300000

const results = []
const t0 = Date.now()

const now = () => performance.now()
const sec = (ms) => (ms / 1000).toFixed(1)

async function call(method, path, body, { form } = {}) {
  const opts = { method, signal: AbortSignal.timeout(TIMEOUT) }
  if (form) {
    opts.body = form
  } else if (body !== undefined) {
    opts.headers = { 'Content-Type': 'application/json' }
    opts.body = JSON.stringify(body)
  }
  const started = now()
  const res = await fetch(BASE + path, opts)
  const text = await res.text()
  const ms = now() - started
  let json = null
  try {
    json = JSON.parse(text)
  } catch {
    throw new Error(`响应不是 JSON（HTTP ${res.status}）：${text.slice(0, 120)}`)
  }
  if (json.code !== 'success') throw new Error(json.message || `code=${json.code}`)
  return { ms, data: json.data, message: json.message }
}

// 流式对话单独计时：首字（TTFT）与完整回答是两个不同指标
async function streamChat(payload) {
  const started = now()
  const res = await fetch(BASE + '/conversation/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(TIMEOUT),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  let ttft = 0
  let doneMs = 0
  let answer = ''
  let deltaCount = 0
  let error = ''
  let done = null
  for (;;) {
    const { value, done: end } = await reader.read()
    if (end) break
    buf += decoder.decode(value, { stream: true })
    let idx
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const raw = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      const ev = /event: (.*)/.exec(raw)?.[1]
      const dataLine = /data: (.*)/.exec(raw)?.[1]
      if (!ev || !dataLine) continue
      const data = JSON.parse(dataLine)
      if (ev === 'delta') {
        if (!ttft) ttft = now() - started
        deltaCount += 1
        answer += data.text || ''
      } else if (ev === 'error') {
        error = data.message || '未知错误'
      } else if (ev === 'done') {
        doneMs = now() - started
        done = data
      }
    }
  }
  if (error) throw new Error(`${error}（已收到 ${deltaCount} 个增量、${answer.length} 字）`)
  // agent 命中工具链时最终回答整段下发；完全没回答时后端用兜底文案填 done.answer
  if (!answer && done?.answer) answer = done.answer
  if (!answer) throw new Error('没有收到任何回答内容')
  // done 之后的 verification 是另一次小模型调用，用户已经读到答案了，
  // 但它会让 SSE 连接继续挂着——单独量出来，否则总耗时会被它全部吃掉
  return { ttft, doneMs, endMs: now() - started, answer, deltaCount, done }
}

async function step(name, fn, note = '') {
  process.stdout.write(`  ▸ ${name} … `)
  const started = now()
  try {
    const detail = await fn()
    const ms = now() - started
    results.push({ name, ms, ok: true, note, detail: detail || '' })
    console.log(`${sec(ms)}s`)
    return detail
  } catch (err) {
    const ms = now() - started
    results.push({ name, ms, ok: false, note, detail: err.message })
    console.log(`失败（${sec(ms)}s）：${err.message}`)
    return null
  }
}

const JD = `岗位：Python 后端工程师（AI 应用方向）
职责：
1. 负责 LLM 应用后端服务的设计与开发，包括对话、检索增强（RAG）与工具调用链路；
2. 负责流式接口、任务编排与失败重试，保证长任务的可观测与可恢复；
3. 参与事务治理与数据一致性方案设计，优化慢查询与索引。
要求：
1. 3 年以上 Python 服务端经验，熟悉 FastAPI / SQLAlchemy；
2. 熟悉 PostgreSQL、Redis，理解事务隔离级别与锁；
3. 有向量检索（FAISS / pgvector）与提示词工程实践经验；
4. 熟悉 Docker 部署与 CI 流程。`

const RESUME = `张三｜Python 后端工程师
技能：Python / FastAPI / SQLAlchemy / PostgreSQL / Redis / FAISS / LangChain
项目：Job Copilot 求职助手 Agent 平台
- 设计流式对话接口（SSE），首字延迟从 3.2s 降到 0.9s；
- 用两段事务拆分 LLM 生成与写库，避免长事务持有连接；
- 维护简历/JD 向量索引，检索召回率提升 18%。`

const PROJECT = `我负责的模块是简历与 JD 的匹配分析。原来的实现把 LLM 调用放在数据库事务里，
一次分析要持有连接 40 秒以上，并发一高就把连接池打满。我把它拆成两段事务：先在短事务里
读取简历与 JD 快照，出事务后再调模型，最后用新事务写结果。同时给分析结果加了降级标记，
模型漏字段时标记 degraded 而不是直接报错。改造后 P95 从 46s 降到 12s，连接池打满的告警消失。`

const main = async () => {
  console.log(`base = ${BASE}`)
  const modelInfo = await call('GET', '/models').then((r) => r.data).catch(() => null)
  if (modelInfo) console.log(`模型：默认 ${modelInfo.default}（可选 ${modelInfo.models.join(' / ')}）`)
  console.log(`开始：${new Date().toLocaleString('zh-CN')}\n`)

  // ---- 准备：一个临时会话，跑完删除 ----
  const created = await step('新建会话', async () => {
    const r = await call('POST', '/conversation/create', { title: 'tmp-bench-模块耗时', mode: 'agent' })
    return r.data.conversation_id
  })
  if (!created) process.exit(1)
  const cid = created

  // ---- 1. 文档上传（解析 + 分块 + 建索引） ----
  await step(
    '文档上传 → 解析入库',
    async () => {
      const form = new FormData()
      form.append('conversation_id', cid)
      form.append('file', new Blob([RESUME], { type: 'text/plain' }), 'bench_简历.txt')
      const r = await call('POST', '/document/upload', undefined, { form })
      const doc = r.data.document || {}
      return `字符数 ${doc.char_count ?? '?'}，状态 ${doc.status ?? '?'}`
    },
    'PDF/Word 会更慢，取决于解析器'
  )

  // ---- 2. 知识库检索（纯检索，无 LLM） ----
  await step('知识库检索（Top3）', async () => {
    const r = await call('GET', `/knowledge/query?query=${encodeURIComponent('FastAPI 事务 连接池')}&k=3`)
    return `命中 ${r.data.total} 条`
  })

  // ---- 3. 对话：流式（agent 会先做工具决策，rag 直接检索后作答） ----
  const ask = '用三句话说明这个项目里你负责的部分最难的技术点。'
  await step('对话·流式 agent（含工具决策）', async () => {
    const r = await streamChat({ conversation_id: cid, query: ask, mode: 'agent' })
    results.push({ name: '  └ agent 首字（TTFT）', ms: r.ttft, ok: true, note: '用户感知的"开始响应"', detail: '' })
    results.push({ name: '  └ agent 回答结束（done）', ms: r.doneMs, ok: true, note: '用户读到完整回答', detail: '' })
    const warnings = r.done?.warnings || []
    return `${r.answer.length} 字 / ${r.deltaCount} 个增量；done 后校验再花 ${sec(r.endMs - r.doneMs)}s${warnings.length ? `；降级：${warnings.join('、')}` : ''}`
  })
  await step('对话·流式 rag（检索直答）', async () => {
    const r = await streamChat({ conversation_id: cid, query: ask, mode: 'rag' })
    results.push({ name: '  └ rag 首字（TTFT）', ms: r.ttft, ok: true, note: '用户感知的"开始响应"', detail: '' })
    results.push({ name: '  └ rag 回答结束（done）', ms: r.doneMs, ok: true, note: '用户读到完整回答', detail: '' })
    return `${r.answer.length} 字 / ${r.deltaCount} 个增量；done 后校验再花 ${sec(r.endMs - r.doneMs)}s`
  })

  // ---- 4. 对话：非流式 ----
  await step('对话·非流式（等完整回答）', async () => {
    const r = await call('POST', '/conversation/chat', {
      conversation_id: cid,
      query: '一句话总结这份 JD 最看重的两项能力。',
      mode: 'agent',
    })
    return `${(r.data.answer || '').length} 字`
  })

  // ---- 5. JD 分析 ----
  let analysisId = 0
  await step('JD 分析（抽取 + 匹配评分）', async () => {
    const r = await call('POST', '/job/analyze', { conversation_id: cid, jd_text: JD })
    analysisId = r.data.analysis_id
    return `analysis_id=${analysisId}`
  })

  // ---- 6. 招聘截图分析（可选，需传入一张 JD 截图） ----
  if (SHOT) {
    await step('招聘截图 → JD 分析', async () => {
      const form = new FormData()
      form.append('conversation_id', cid)
      form.append('file', new Blob([readFileSync(SHOT)], { type: 'image/png' }), basename(SHOT))
      const up = await call('POST', '/conversation/image/upload', undefined, { form })
      const r = await call('POST', '/job/analyze-from-screenshot', {
        conversation_id: cid,
        image_url: up.data.image_url,
      })
      return `analysis_id=${r.data.analysis_id}`
    })
  }

  // ---- 7. 项目优化 ----
  await step('项目优化（重写 + 双版本）', async () => {
    const r = await call('POST', '/resume/project/rewrite', {
      conversation_id: cid,
      project_description: PROJECT,
    })
    return `rewrite_id=${r.data.rewrite_id}`
  })

  // ---- 8. 模拟面试：开始 + 逐轮作答到总结 ----
  let sessionId = ''
  let questionIndex = 0
  let totalQuestions = 0
  await step('模拟面试·开始（生成题目）', async () => {
    const r = await call('POST', '/interview/start', {
      conversation_id: cid,
      direction: 'general',
      jd_text: JD,
    })
    sessionId = r.data.session_id
    questionIndex = r.data.question_index ?? 0
    totalQuestions = r.data.total_questions ?? 0
    return `共 ${totalQuestions} 题，首题「${(r.data.current_question?.question || '').slice(0, 18)}…」`
  })

  const ANSWER = `我在 Job Copilot 里负责后端链路。最直接的一次是面试作答接口：原实现把用户回答、
模型评价、下一题生成和轮次更新全放在一个事务里，模型一次要 20 秒以上，连接被占着打满连接池。
我拆成读快照、出事务调模型、再新事务写结果三段，并对重复提交按轮次做幂等校验，避免同一题被评价两次。
结果 P95 从 40 秒级降到十几秒，超时告警基本消失。`

  const maxRounds = Math.min(totalQuestions || 4, 6)
  for (let i = 0; i < maxRounds; i++) {
    const r = await step(`模拟面试·第 ${i + 1} 轮作答（评价 + 下一题）`, async () => {
      const res = await call('POST', '/interview/answer', {
        session_id: sessionId,
        answer: ANSWER,
        question_index: questionIndex,
      })
      questionIndex = res.data.question_index ?? questionIndex
      const action = res.data.action
      return `action=${action}${action === 'summary' ? `，总分 ${res.data.score}` : ''}`
    })
    if (!r || r.includes('action=summary')) break
  }

  // ---- 9. 导出与只读查询（无 LLM） ----
  await step('导出：面试记录 Markdown', async () => {
    const r = await call('GET', `/export/interview?session_id=${sessionId}`)
    return `${(r.data.markdown || '').length} 字符`
  })
  await step('导出：JD 分析 Markdown', async () => {
    const r = await call('GET', `/export/analysis?analysis_id=${analysisId}`)
    return `${(r.data.markdown || '').length} 字符`
  })
  await step('简历版本列表', async () => {
    const r = await call('GET', `/resume/versions/list?conversation_id=${cid}`)
    const n = (r.data.versions || []).length
    return `${n} 个版本`
  })
  await step('知识库状态', async () => {
    const r = await call('GET', '/knowledge/status')
    return `${r.data.total_documents} 条，索引${r.data.index_exists ? '已建' : '缺失'}`
  })

  // ---- 清理 ----
  await step('删除测试会话（含级联数据）', async () => {
    await call('POST', '/conversation/delete', { conversation_id: cid })
    return '已清理'
  })

  // ---- 报告 ----
  const okRows = results.filter((r) => r.ok)
  const failRows = results.filter((r) => !r.ok)
  const total = Date.now() - t0

  console.log('\n模块 / 步骤'.padEnd(34) + '耗时'.padStart(8) + '  说明')
  console.log('-'.repeat(96))
  for (const r of results) {
    const mark = r.ok ? '' : '✗ '
    console.log(
      (mark + r.name).padEnd(34) +
        `${sec(r.ms)}s`.padStart(8) +
        `  ${r.ok ? r.detail : `失败：${r.detail}`}${r.note ? `｜${r.note}` : ''}`
    )
  }
  console.log('-'.repeat(96))
  console.log(`合计 ${sec(total)}s：成功 ${okRows.length} 项，失败 ${failRows.length} 项`)

  const md = [
    '# 模块耗时实测',
    '',
    `- 采集时间：${new Date().toLocaleString('zh-CN')}`,
    `- 方式：直接调用后端接口（\`${BASE}\`），计时为"发起请求 → 结果完整返回"的墙钟时间`,
    `- 环境：本机，单次串行执行，非并发压测；LLM 耗时会随模型负载与配额降级波动`,
    modelInfo ? `- 模型：默认 \`${modelInfo.default}\`（当前账号可选 ${modelInfo.models.join(' / ')}）` : '',
    '',
    '| 模块 / 步骤 | 耗时 | 结果 / 说明 |',
    '| --- | --- | --- |',
    ...results.map((r) => {
      const d = r.ok ? r.detail : `**失败**：${r.detail}`
      return `| ${r.name} | ${sec(r.ms)}s | ${d}${r.note ? `（${r.note}）` : ''} |`
    }),
    '',
    `合计 **${sec(total)}s**，成功 ${okRows.length} 项、失败 ${failRows.length} 项。`,
    '',
    '## 怎么读这张表',
    '',
    '- **TTFT ≠ 总耗时**：对话的"开始有字"和"回答完"是两件事，用户等的是前者。',
    '- **agent 与 rag 是两条链路**：agent 先做工具决策（可能连续调用多轮工具），rag 直接检索后作答。',
    '  agent 一旦工具轮次跑满却没有产出最终回答，就会退化成兜底文案（表里标"降级"），这时耗时虽长但没有结果。',
    '- **面试是链式的**：一轮作答里包含评价生成 + 下一题生成两次模型调用，所以单轮比一次问答慢；',
    '  整场面试的总时长约等于「开始 + 轮数 × 单轮」，这个数值直接决定用户会不会中途放弃。',
    '- **纯检索/导出都在亚秒级**：说明慢的部分全在模型调用，不在数据库或文件 IO。',
  ].join('\n')
  writeFileSync('docs/2026-10-18-模块耗时实测.md', md)
  console.log('\n报告已写入 docs/2026-10-18-模块耗时实测.md')
}

main().catch((e) => {
  console.error('基准失败：', e)
  process.exitCode = 1
})
