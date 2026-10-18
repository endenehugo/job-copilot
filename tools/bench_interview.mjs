// 面试链路实测：开始 → 3 轮作答，逐段计时，校验评分与结构是否仍正常。
// 用法：node tools/bench_interview.mjs [--rounds=3]
import { readFileSync } from 'node:fs'

const args = Object.fromEntries(
  process.argv.slice(2).map((a) => {
    const [k, ...v] = a.replace(/^--/, '').split('=')
    return [k, v.join('=')]
  })
)
const BASE = args.base || 'http://127.0.0.1:8018/api/v1'
const ROUNDS = Number(args.rounds || 3)
const TIMEOUT = 300000

const RESUME = `张三｜Python 后端工程师
技能：Python / FastAPI / SQLAlchemy / PostgreSQL / Redis / FAISS / LangChain
项目：Job Copilot 求职助手 Agent 平台
- 设计流式对话接口（SSE），首字延迟从 3.2s 降到 0.9s；
- 用两段事务拆分 LLM 生成与写库，避免长事务持有连接；
- 维护简历/JD 向量索引，检索召回率提升 18%。`

const JD = `岗位：AI 应用工程师（Agent 方向）
职责：负责 LLM 应用后端服务设计与开发，熟悉 FastAPI 异步框架；参与 RAG 检索链路建设（文档解析、向量索引、召回重排）；保障服务稳定性，具备连接池治理与事务降级经验。
要求：3 年以上 Python 经验，熟悉 PostgreSQL / Redis；有 Agent 工具编排、SSE 流式接口实战经验者优先。`

const ANSWERS = [
  '原来的实现把 LLM 调用放在数据库事务里，一次分析要持有连接 40 秒以上，并发一高就把连接池打满。我把它拆成两段事务：先在短事务里读简历与 JD 快照，出事务后再调模型，最后用新事务写结果。同时给结果加了降级标记，模型漏字段时标记 degraded 而不是报错。改造后 P95 从 46 秒降到 12 秒。',
  'RAG 这边我做了三件事：文档解析后按 500 字切块并保留标题路径；用 FAISS 建索引，检索时先向量召回 Top10 再用模型做 listwise 重排；最后把命中片段带上来源路径一起下发给前端。中间踩过 BM25 按字切分导致召回质量差的问题，所以没有默认用 RRF。',
  '稳定性上我做了两件事：一是所有 LLM 调用统一走工厂，把超时从最坏 50 分钟压到 60 秒，重试次数从 10 降到 2；二是做了模型降级链，额度或权限报错时沿链路粘性切换，避免免费额度耗尽后整个功能不可用。',
]

const now = () => performance.now()
const sec = (ms) => (ms / 1000).toFixed(2)

async function api(method, path, body, form) {
  const opts = { method, signal: AbortSignal.timeout(TIMEOUT) }
  if (form) opts.body = form
  else if (body !== undefined) {
    opts.headers = { 'Content-Type': 'application/json' }
    opts.body = JSON.stringify(body)
  }
  const t = now()
  const res = await fetch(BASE + path, opts)
  const json = await res.json()
  const ms = now() - t
  if (json.code !== 'success') throw new Error(`${path} → ${json.message}`)
  return { ms, data: json.data, warnings: json.warnings }
}

const rows = []
const cid = (await api('POST', '/conversation/create', { title: 'tmp-bench-interview', mode: 'agent' }))
  .data.conversation_id

const form = new FormData()
form.append('conversation_id', cid)
form.append('file', new Blob([RESUME], { type: 'text/plain' }), 'bench_简历.txt')
await api('POST', '/document/upload', undefined, form)

const started = await api('POST', '/interview/start', { conversation_id: cid, direction: 'agent_ai', jd_text: JD })
const qs = started.data.questions || []
rows.push({ name: '开始面试（出题）', ms: started.ms, note: `${qs.length} 题，首题「${(qs[0]?.question || '').slice(0, 24)}…」` })
console.log(`开始：${sec(started.ms)}s，${qs.length} 题`)

let sessionId = started.data.session_id
let index = 0
for (let i = 0; i < ROUNDS; i++) {
  const r = await api('POST', '/interview/answer', {
    session_id: sessionId,
    answer: ANSWERS[i % ANSWERS.length],
    question_index: index,
  })
  const d = r.data
  rows.push({
    name: `第 ${i + 1} 轮作答`,
    ms: r.ms,
    note: `action=${d.action} score=${d.score} 评价 ${(d.evaluation || '').length} 字${r.warnings?.length ? `，警告：${r.warnings.join('、')}` : ''}`,
  })
  console.log(`第 ${i + 1} 轮：${sec(r.ms)}s  score=${d.score}  ${(d.evaluation || '').slice(0, 40)}…`)
  if (d.action === 'summary') break
  index = d.question_index
}

console.log('\n汇总：')
for (const r of rows) console.log(`  ${r.name.padEnd(14)} ${sec(r.ms).padStart(6)}s  ${r.note}`)
const total = rows.reduce((s, r) => s + r.ms, 0)
console.log(`  合计 ${sec(total)}s`)

await api('POST', '/conversation/delete', { conversation_id: cid })
console.log('临时会话已删除')
