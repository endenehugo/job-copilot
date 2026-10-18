// 前端链路冒烟（P2）：验证「主会话里的简历卡片 → 打开预览 → 刷新后仍在」。
// 与 ui_resume_smoke.mjs 的区别：那个验证"能生成"，这个验证"落库 + 嵌套"，
// 不需要再跑一次 LLM（直接复用会话里已有的生成记录）。
//
// 用法：node tools/ui_resume_persist_smoke.mjs [baseUrl] [outPrefix]

const BASE = process.argv[2] || 'http://127.0.0.1:5174/'
const PREFIX = process.argv[3] || 'tools/fixtures/ui-resume-persist'
const CDP = 'http://127.0.0.1:9222'
const CONV_TITLE = 'E2E 真实素材验证'

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function connect() {
  let tabs
  for (let i = 0; i < 30; i++) {
    try {
      tabs = await (await fetch(`${CDP}/json`)).json()
      break
    } catch {
      await sleep(500)
    }
  }
  if (!tabs) throw new Error('CDP 未就绪')
  const page = tabs.find((t) => t.type === 'page')
  if (!page) throw new Error('没有可用的页面 target')

  const ws = new WebSocket(page.webSocketDebuggerUrl)
  const pending = new Map()
  let seq = 0
  const send = (method, params = {}) =>
    new Promise((resolve, reject) => {
      const id = ++seq
      pending.set(id, { resolve, reject })
      ws.send(JSON.stringify({ id, method, params }))
    })
  ws.addEventListener('message', (ev) => {
    const msg = JSON.parse(ev.data)
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id)
      pending.delete(msg.id)
      msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result)
    }
  })
  await new Promise((r) => ws.addEventListener('open', r))

  const evaluate = async (expression) => {
    const res = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
    if (res.exceptionDetails) throw new Error(res.exceptionDetails.text)
    return res.result?.value
  }
  const shot = async (file) => {
    const { data } = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true })
    const { writeFileSync } = await import('node:fs')
    writeFileSync(file, Buffer.from(data, 'base64'))
    console.log('   截图：', file)
  }
  return { ws, send, evaluate, shot }
}

async function waitFor(evaluate, expr, label, tries = 20, gap = 600) {
  for (let i = 0; i < tries; i++) {
    if (await evaluate(expr)) return true
    await sleep(gap)
  }
  console.log(`   等待超时：${label}`)
  return false
}

async function main() {
  const { ws, send, evaluate, shot } = await connect()
  await send('Page.enable')
  await send('Runtime.enable')
  await send('Page.navigate', { url: BASE })

  await waitFor(evaluate, `document.querySelectorAll('.conv-row').length > 0`, '会话列表', 30, 700)

  // 1) 侧栏会话树里应能看到「简历」分组与计数
  // 展开状态存在 localStorage，脚本可能重复运行：已经展开时再点箭头会收起
  const tree = await evaluate(`(() => {
    const row = [...document.querySelectorAll('.conv-row')]
      .find(el => el.textContent.includes('${CONV_TITLE}'));
    if (!row) return { error: 'no-conversation' };
    const node = row.closest('.conv-node');
    const expanded = !!node.querySelector('.conv-group-head');
    if (!expanded) {
      const caret = row.querySelector('.conv-caret');
      if (caret) caret.click();
    }
    return { badge: row.querySelector('.conv-badge')?.textContent.trim() || '', expanded };
  })()`)
  console.log('1. 会话行：', JSON.stringify(tree))
  await sleep(1200)

  const groups = await evaluate(`(() => {
    const items = [...document.querySelectorAll('.conv-group-head')].map(el => el.textContent.replace(/\\s+/g, ' ').trim());
    return items;
  })()`)
  console.log('   侧栏分组：', JSON.stringify(groups))

  // 展开「简历」组
  const resumeGroup = await evaluate(`(() => {
    const head = [...document.querySelectorAll('.conv-group-head')].find(el => el.textContent.includes('简历'));
    if (!head) return 'no-group';
    // 前面点过行的箭头「展开全部」，此时组已经是打开的；重复点会把它关掉
    const caret = head.querySelector('.conv-group-caret');
    const open = !!(caret && caret.classList.contains('open'));
    if (!open) head.click();
    return open ? 'already-open' : 'clicked';
  })()`)
  await sleep(1200)
  const resumeNodes = await evaluate(`(() => {
    const head = [...document.querySelectorAll('.conv-group-head')].find(el => el.textContent.includes('简历'));
    if (!head) return [];
    const group = head.closest('.conv-group');
    return [...group.querySelectorAll('.conv-child-title')].map(el => el.textContent.trim());
  })()`)
  console.log('2. 简历分组：', resumeGroup, JSON.stringify(resumeNodes))

  // 2) 进会话，滚到底部看卡片
  const picked = await evaluate(`(() => {
    const row = [...document.querySelectorAll('.conv-row')]
      .find(el => el.textContent.includes('${CONV_TITLE}'));
    if (!row) return 'no-conversation';
    row.click();
    return 'ok';
  })()`)
  console.log('3. 进入会话：', picked)
  await sleep(2000)

  await waitFor(
    evaluate,
    `!!document.querySelector('.resume-card')`,
    '会话内简历卡片',
    15,
    600
  )
  await evaluate(`(() => {
    const cards = document.querySelectorAll('.resume-card');
    cards[cards.length - 1]?.scrollIntoView({ block: 'center' });
    return cards.length;
  })()`)
  await sleep(600)

  const card = await evaluate(`(() => {
    const el = [...document.querySelectorAll('.resume-card')].pop();
    if (!el) return { error: 'no-card' };
    return {
      title: el.querySelector('.rc-title')?.textContent.trim(),
      meta: el.querySelector('.rc-meta')?.textContent.replace(/\\s+/g, ' ').trim(),
      buttons: [...el.querySelectorAll('.rc-btn')].map(b => b.textContent.trim()),
    };
  })()`)
  console.log('4. 卡片内容：', JSON.stringify(card, null, 2))
  await shot(`${PREFIX}-card.png`)

  // 3) 点「打开预览」
  const opened = await evaluate(`(() => {
    const el = [...document.querySelectorAll('.resume-card')].pop();
    const btn = el && [...el.querySelectorAll('.rc-btn')].find(b => b.textContent.includes('打开预览'));
    if (!btn) return 'no-button';
    btn.click(); return 'ok';
  })()`)
  console.log('5. 打开预览：', opened)

  const ready = await waitFor(
    evaluate,
    `(() => { const f = document.querySelector('.paper-frame'); return !!(f && f.srcdoc && f.srcdoc.length > 2000); })()`,
    '简历预览',
    20,
    600
  )
  await sleep(1200)
  const preview = await evaluate(`(() => {
    const f = document.querySelector('.paper-frame');
    return {
      title: document.querySelector('.rgen-title')?.textContent.replace(/\\s+/g, ' ').trim(),
      srcdocLen: f?.srcdoc?.length || 0,
      height: f ? Math.round(f.getBoundingClientRect().height) : 0,
      hasFillBtn: [...document.querySelectorAll('button')].some(b => b.textContent.includes('补上这些字段')),
    };
  })()`)
  console.log('6. 预览状态：', ready ? JSON.stringify(preview, null, 2) : '未就绪')
  await shot(`${PREFIX}-preview.png`)

  // 4) 刷新页面：卡片必须还在（这是 P2 的核心验收点）
  await send('Page.reload')
  await waitFor(evaluate, `document.querySelectorAll('.conv-row').length > 0`, '刷新后的会话列表', 30, 700)
  await evaluate(`(() => {
    const row = [...document.querySelectorAll('.conv-row')]
      .find(el => el.textContent.includes('${CONV_TITLE}'));
    row && row.click();
    return !!row;
  })()`)
  await sleep(2500)
  const afterReload = await evaluate(`(() => {
    const cards = [...document.querySelectorAll('.resume-card')];
    return { count: cards.length, title: cards.length ? cards[0].querySelector('.rc-title')?.textContent.trim() : '' };
  })()`)
  console.log('7. 刷新后卡片：', JSON.stringify(afterReload))

  const persistOk = afterReload.count > 0
  console.log(persistOk ? '\n结果：持久化验证通过' : '\n结果：刷新后卡片丢失')

  ws.close()
  if (!persistOk) process.exit(1)
}

main().catch((err) => {
  console.error('冒烟失败：', err.message)
  process.exit(1)
})
