// 真实素材 E2E：JD 分析 → 模拟面试 → 项目改写，逐段计时并留存 JSON。
//
// 素材来自公网：腾讯招聘真实 JD（企业微信-AI 应用后台开发）+ 公开的 3 年 AI 应用工程师简历。
// 用法：node tools/e2e_fixtures.mjs [--base=http://127.0.0.1:8018/api/v1] [--out=tools/fixtures/last-run.json]
import fs from 'node:fs'
import path from 'node:path'

const args = Object.fromEntries(
  process.argv.slice(2).map((a) => {
    const [k, ...v] = a.replace(/^--/, '').split('=')
    return [k, v.join('=')]
  })
)
const BASE = args.base || 'http://127.0.0.1:8018/api/v1'
const OUT = args.out || 'tools/fixtures/last-run.json'
const TIMEOUT = 300000

const JD = fs.readFileSync('tools/fixtures/jd-tencent-wecom-ai-backend.txt', 'utf8')
const RESUME = fs.readFileSync('tools/fixtures/resume-ai-app-3y.txt', 'utf8')
const PROJECT = `一、企业级智能知识库问答系统（AI 应用开发工程师，2024.01 - 2026.01）
- 主导 RAG 全流程开发，实现多格式文档解析、语义切片、文心一言 Embedding 向量化与 Milvus 检索。
- 开发核心接口与服务，优化响应速度；通过提示词工程优化回答精准度，迭代产品体验。
- Milvus + 文心一言 RAG 架构，问答准确率 90%+，支持百万级文档秒级检索，响应 ≤2s。
- 接入 3 个核心部门，日均调用 1w+，人工咨询量下降 60%。`

const now = () => performance.now()
const ms = (t) => Math.round(now() - t)
const sec = (n) => (n / 1000).toFixed(2)

async function api(method, p, body, form) {
  const opts = { method, signal: AbortSignal.timeout(TIMEOUT) }
  if (form) opts.body = form
  else if (body !== undefined) {
    opts.headers = { 'Content-Type': 'application/json' }
    opts.body = JSON.stringify(body)
  }
  const t = now()
  const res = await fetch(BASE + p, opts)
  const json = await res.json()
  const cost = ms(t)
  if (json.code !== 'success') throw new Error(`${p} → [${res.status}] ${json.message}`)
  return { cost, data: json.data, warnings: json.warnings }
}

// SSE：记录每个事件到达的相对时间，返回事件数组。
async function stream(p, body) {
  const t = now()
  const res = await fetch(BASE + p, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(TIMEOUT),
  })
  if (!res.headers.get('content-type')?.includes('text/event-stream')) {
    const json = await res.json()
    throw new Error(`${p} 非流式响应：${json.message}`)
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  const events = []
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let idx
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const frame = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      let event = 'message'
      const dataLines = []
      for (const line of frame.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
      }
      if (!dataLines.length) continue
      events.push({ event, at: ms(t), data: JSON.parse(dataLines.join('\n')) })
    }
  }
  const err = events.find((e) => e.event === 'error')
  if (err) throw new Error(`${p} 流内错误：${err.data.message}`)
  return { total: ms(t), events }
}

const result = { startedAt: new Date().toISOString(), base: BASE, steps: {} }
const log = (...a) => console.log(...a)

// ---------- 0. 建会话 + 上传简历 ----------
const conv = await api('POST', '/conversation/create', {
  title: 'E2E 真实素材验证（腾讯 JD + 3 年 AI 应用简历）',
  mode: 'agent',
})
const cid = conv.data.conversation_id
log(`[0] 会话 ${cid} 创建 ${conv.cost}ms`)

const form = new FormData()
form.append('conversation_id', cid)
form.append('file', new Blob([RESUME], { type: 'text/plain' }), 'resume-ai-app-3y.txt')
const up = await api('POST', '/document/upload', undefined, form)
log(`[0] 简历上传+解析+索引 ${up.cost}ms，doc=${up.data.document.document_id}`)
result.steps.setup = { conversationId: cid, uploadMs: up.cost, documentId: up.data.document.document_id }

// ---------- 1. JD 分析 ----------
const jdStream = await stream('/job/analyze/stream', { conversation_id: cid, jd_text: JD })
const done = jdStream.events.find((e) => e.event === 'done').data
const stages = jdStream.events.filter((e) => e.event === 'stage').map((e) => ({ ...e.data, at: e.at }))
log('[1] JD 分析', sec(jdStream.total), 's，阶段：', stages.map((s) => `${s.stage}@${s.at}ms`).join(' '))
log('    评分', done.scoring.total_score, '| 维度', JSON.stringify(done.scoring.dimensions))
result.steps.jdAnalyze = {
  totalMs: jdStream.total,
  stages,
  scoring: done.scoring,
  jdAnalysis: done.jd_analysis,
}

const latest = await api('GET', `/job/analysis/latest?conversation_id=${cid}`)
result.steps.jdAnalyze.persisted = {
  analysisId: latest.data.id,
  totalScore: latest.data.total_score,
  dimensions: latest.data.dimensions,
}

// ---------- 2. 模拟面试 ----------
// 两场：A 等题库补齐（真实节奏：用户看完题再打字），B 立刻作答（验证 pending 降级 + 差回答追问）。
const GOOD_ANSWERS = [
  `RAG 这块我负责全链路：先用自研解析器把 PDF/Word 按标题层级切成 500 字左右的块，保留章节路径；然后调 embedding 写入 Milvus，检索走 Top20 向量召回，再用模型做 listwise 重排取 Top5。上线后首答准确率从 71% 提到 90%，P95 响应控制在 2 秒内。中间踩过一个坑：BM25 按字切分中文，召回噪声大，所以没有默认启用 RRF 融合，而是先做语义重排。`,
  `检索质量我是分层评估的：召回层看 recall@10，重排层看命中片段是否被最终引用，生成层再人工抽检忠实度。评测集是从两个部门的真实工单里抽的 300 条问题，每次改切片或重排策略都跑一遍，避免"感觉变好了"就上线。`,
  `并发这块我把 LLM 调用从数据库事务里拆出去了：原来一次分析要持连接 40 秒，QPS 一高连接池就打满。拆成读快照、出事务调模型、新事务落库之后，P95 从 46 秒降到 12 秒。另外给模型调用加了统一工厂，超时压到 60 秒、重试 2 次，并做了额度耗尽时的降级链，避免功能整体不可用。`,
]
const VAGUE_ANSWERS = [`就是把文档切一切塞给模型，没怎么调优。`]

async function waitBankReady(sessionId, timeoutMs = 25000) {
  const t0 = now()
  while (now() - t0 < timeoutMs) {
    const d = await api('GET', `/interview/detail?session_id=${sessionId}`)
    const b = d.data.session.question_bank
    if (b.state === 'ready' || b.state === 'failed') return { bank: b, readyMs: ms(t0) }
    await new Promise((r) => setTimeout(r, 400))
  }
  return { bank: null, readyMs: null }
}

async function runInterview(label, { answers, waitBank }) {
  const start = await api('POST', '/interview/start', {
    conversation_id: cid,
    direction: 'general',
    jd_text: JD,
  })
  const sid = start.data.session_id
  const q0 = start.data.current_question?.question || ''
  log(`[2${label}] 开场 ${start.cost}ms → ${sid}`)
  log(`     Q1: ${q0.slice(0, 70)}...`)
  const item = {
    sessionId: sid,
    startMs: start.cost,
    firstQuestion: q0,
    bankWaitMs: null,
    bankAfterWait: null,
    rounds: [],
    stoppedBy: null,
  }

  if (waitBank) {
    const w = await waitBankReady(sid)
    item.bankWaitMs = w.readyMs
    item.bankAfterWait = w.bank
    log(`     题库补齐等待 ${w.readyMs}ms，状态 ${w.bank?.state} 共 ${w.bank?.size} 题`)
  }

  for (let i = 0; i < answers.length; i++) {
    const s = await stream('/interview/answer/stream', {
      session_id: sid,
      answer: answers[i],
      question_index: i,
    })
    const doneEv = s.events.find((e) => e.event === 'done').data
    const row = {
      round: i + 1,
      totalMs: s.total,
      firstTokenMs: s.events.find((e) => e.event === 'evaluation')?.at ?? null,
      score: s.events.find((e) => e.event === 'score')?.data.score ?? null,
      questionSource: s.events.find((e) => e.event === 'question')?.data.source ?? doneEv.question_source ?? null,
      nextQuestion: (s.events.find((e) => e.event === 'question')?.data.question || doneEv.next_question || '').slice(0, 110),
      action: doneEv.action,
      evaluation: doneEv.evaluation || '',
      answer: answers[i],
    }
    item.rounds.push(row)
    log(
      `     R${i + 1} ${sec(s.total)}s 首字 ${row.firstTokenMs}ms 评分 ${row.score} 来源 ${row.questionSource} action=${row.action}`
    )
    if (doneEv.action === 'summary') {
      item.stoppedBy = 'summary'
      break
    }
  }

  // 落库与题库状态
  await new Promise((r) => setTimeout(r, 1200))
  const detail = await api('GET', `/interview/detail?session_id=${sid}`)
  item.bank = detail.data.session.question_bank
  item.status = detail.data.session.status
  item.roundCount = detail.data.session.round_count
  item.totalScore = detail.data.session.total_score
  item.overallSummary = detail.data.session.overall_summary
  item.messages = (detail.data.messages || []).map((m) => ({
    role: m.role,
    type: m.msg_type,
    score: m.score,
    content: (m.content || '').slice(0, 100),
  }))
  return item
}

result.steps.interview = {
  A: await runInterview('A', { answers: GOOD_ANSWERS, waitBank: true }),
  B: await runInterview('B', { answers: VAGUE_ANSWERS, waitBank: false }),
}

// ---------- 3. 项目改写 ----------
const rw = await api('POST', '/resume/project/rewrite', {
  conversation_id: cid,
  project_description: PROJECT,
})
log(`[3] 项目改写 ${rw.cost}ms，rewrite_id=${rw.data.rewrite_id}`)
log('    issues:', (rw.data.original_issues || []).join(' / '))
log('    通用版:', (rw.data.improved_version || '').slice(0, 100))
const list = await api('GET', `/resume/project/rewrites?conversation_id=${cid}`)
result.steps.rewrite = {
  costMs: rw.cost,
  rewriteId: rw.data.rewrite_id,
  issues: rw.data.original_issues,
  improvedVersion: rw.data.improved_version,
  pythonBackendVersion: rw.data.python_backend_version,
  agentVersion: rw.data.agent_version,
  historyCount: list.data.rewrites?.length ?? list.data.items?.length ?? null,
}

result.finishedAt = new Date().toISOString()
fs.mkdirSync(path.dirname(OUT), { recursive: true })
fs.writeFileSync(OUT, JSON.stringify(result, null, 2), 'utf8')
log(`\n结果已写入 ${OUT}`)
log(`会话 ID: ${cid}`)
