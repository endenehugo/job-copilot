// 前端链路冒烟：用 CDP 驱动无头 Chrome 走一遍「选会话 → 生成简历 → 预览」。
// 只用于本地验证，不属于运行时依赖。
// 用法：node tools/ui_resume_smoke.mjs [baseUrl] [outPng]

const BASE = process.argv[2] || 'http://127.0.0.1:5174/'
const OUT = process.argv[3] || 'tools/fixtures/ui-resume-preview.png'
const CDP = 'http://127.0.0.1:9222'

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function main() {
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
    const res = await send('Runtime.evaluate', {
      expression,
      returnByValue: true,
      awaitPromise: true,
    })
    if (res.exceptionDetails) throw new Error(res.exceptionDetails.text)
    return res.result?.value
  }

  await send('Page.enable')
  await send('Runtime.enable')
  await send('Page.navigate', { url: BASE })

  // 等会话列表渲染出来（后端刚重启时首次列表请求可能较慢）
  for (let i = 0; i < 20; i++) {
    await sleep(700)
    const has = await evaluate(`document.querySelectorAll('.conv-row').length > 0`)
    if (has) break
  }

  // 1) 选中含解析简历的会话（列表里标题以 E2E 真实素材验证 开头的第一项）
  const picked = await evaluate(`(() => {
    const row = [...document.querySelectorAll('.conv-row')]
      .find(el => el.textContent.includes('E2E 真实素材验证'));
    if (!row) return 'no-conversation';
    row.click();
    return row.textContent.trim().slice(0, 30);
  })()`)
  console.log('1. 选中会话：', picked)
  await sleep(1800)

  // 2) 点侧栏「生成简历」
  const opened = await evaluate(`(() => {
    const btn = [...document.querySelectorAll('.sidebar-nav button')]
      .find(b => b.textContent.includes('生成简历'));
    if (!btn) return 'no-nav';
    btn.click(); return 'ok';
  })()`)
  console.log('2. 打开弹窗：', opened)
  await sleep(900)

  // 3) 弹窗里点「生成简历」主按钮
  const started = await evaluate(`(() => {
    // 弹窗里 tab 按钮文案也叫「生成简历」，且四个表单都常驻 DOM（v-show），
    // 必须限定到简历表单自己的提交按钮
    const btn = document.querySelector('.start-form--resume .el-button--primary');
    if (!btn) return 'no-button';
    if (btn.disabled) return 'disabled';
    btn.click(); return 'ok';
  })()`)
  console.log('3. 触发生成：', started)
  await sleep(1200)
  const afterClick = await evaluate(`(() => {
    const btn = document.querySelector('.start-form--resume .el-button--primary');
    return {
      btnText: btn ? btn.textContent.trim() : 'no-btn',
      loadingClass: btn ? btn.className : '',
      dialogOpen: !!document.querySelector('.task-dialog'),
      activeType: document.body.innerHTML.includes('简历预览'),
    };
  })()`)
  console.log('   点后状态：', JSON.stringify(afterClick))

  // 4) 等生成结束：预览 iframe 出现即成功
  let ready = false
  for (let i = 0; i < 60; i++) {
    await sleep(1000)
    ready = await evaluate(`(() => {
      const f = document.querySelector('.paper-frame');
      return !!(f && f.srcdoc && f.srcdoc.length > 2000);
    })()`)
    if (ready) break
  }
  console.log('4. 预览就绪：', ready ? '是' : '否（超时）')
  await sleep(1500)

  const summary = await evaluate(`(() => {
    const f = document.querySelector('.paper-frame');
    const warn = document.querySelector('.notice--warn');
    return {
      title: document.querySelector('.rgen-title')?.textContent.trim().replace(/\\s+/g, ' '),
      frameHeight: f ? f.getBoundingClientRect().height : 0,
      srcdocLen: f?.srcdoc?.length || 0,
      warnCount: warn ? warn.querySelectorAll('li').length : 0,
      hasPrintBtn: !!document.querySelector('.button, button') &&
        [...document.querySelectorAll('button')].some(b => b.textContent.includes('打印')),
    };
  })()`)
  console.log('5. 页面状态：', JSON.stringify(summary, null, 2))

  const shot = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true })
  const { writeFileSync } = await import('node:fs')
  writeFileSync(OUT, Buffer.from(shot.data, 'base64'))
  console.log('截图已保存：', OUT)

  ws.close()
}

main().catch((err) => {
  console.error('冒烟失败：', err.message)
  process.exit(1)
})
