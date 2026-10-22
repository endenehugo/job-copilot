/**
 * 生产环境管理员登录 UI 冒烟（CDP 驱动本机 Chrome）。
 *
 * 用法：先起无头 Chrome（--remote-debugging-port=9222），再
 *   PROD_ADMIN_USER=xxx PROD_ADMIN_PASS=yyy node tools/ui_prod_admin_smoke.mjs
 *
 * 账号密码只从环境变量读，不写进文件（本脚本会进仓库）。
 *
 * 验证链路：首页 → 点左下角用户行 → 填账号密码 → 登录 →
 * 头像/角色文案变「管理员」→ 模型选择器出现管理员专属模型 → 刷新后仍为管理员。
 */
import fs from 'node:fs'
import http from 'node:http'

const BASE = process.env.PROD_BASE || 'https://displaydemo.top/'
const USER = process.env.PROD_ADMIN_USER || ''
const PASS = process.env.PROD_ADMIN_PASS || ''
const SHOT = 'F:/code/job-copilot/tools/fixtures/ui-prod-admin.png'
const CDP = 'http://127.0.0.1:9222'

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function httpJson(url, method = 'GET') {
  return new Promise((resolve, reject) => {
    const req = http.request(url, { method }, (res) => {
      let body = ''
      res.on('data', (c) => (body += c))
      res.on('end', () => {
        try {
          resolve(JSON.parse(body))
        } catch (err) {
          reject(new Error(`JSON 解析失败: ${body.slice(0, 200)}`))
        }
      })
    })
    req.on('error', reject)
    req.end()
  })
}

async function main() {
  if (!USER || !PASS) {
    throw new Error('缺少 PROD_ADMIN_USER / PROD_ADMIN_PASS 环境变量')
  }
  const target = await httpJson(`${CDP}/json/new?about:blank`, 'PUT')
  const ws = new WebSocket(target.webSocketDebuggerUrl)
  let id = 0
  const pending = new Map()

  ws.onmessage = (evt) => {
    const msg = JSON.parse(evt.data)
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id)
      pending.delete(msg.id)
      msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result)
    }
  }

  const send = (method, params = {}) =>
    new Promise((resolve, reject) => {
      const mid = ++id
      pending.set(mid, { resolve, reject })
      ws.send(JSON.stringify({ id: mid, method, params }))
    })

  await new Promise((r) => (ws.onopen = r))
  await send('Page.enable')
  await send('Runtime.enable')

  const evaluate = async (expression) => {
    const res = await send('Runtime.evaluate', {
      expression,
      returnByValue: true,
      awaitPromise: true,
    })
    if (res.exceptionDetails) {
      throw new Error(res.exceptionDetails.exception?.description || 'JS 执行失败')
    }
    return res.result.value
  }

  await send('Page.navigate', { url: BASE })
  for (let i = 0; i < 30; i++) {
    await sleep(600)
    const ready = await evaluate(`!!document.querySelector('.user-row')`)
    if (ready) break
  }
  console.log('1. 页面已加载，用户行存在：', await evaluate(`!!document.querySelector('.user-row')`))
  console.log('   登录前角色文案：', await evaluate(`document.querySelector('.user-role')?.textContent.trim()`))

  // 2) 打开登录弹窗
  await evaluate(`document.querySelector('.user-row').click()`)
  for (let i = 0; i < 20; i++) {
    await sleep(300)
    if (await evaluate(`!!document.querySelector('.app-dialog input')`)) break
  }
  console.log('2. 登录弹窗已打开：', await evaluate(`!!document.querySelector('.app-dialog input')`))

  // 3) 填表单 —— Element Plus 的 input 走 Vue，必须用原生 setter + input 事件
  await evaluate(`(() => {
    const inputs = [...document.querySelectorAll('.app-dialog input')];
    const setValue = (el, val) => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
      setter.call(el, val);
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
    };
    setValue(inputs[0], ${JSON.stringify(USER)});
    setValue(inputs[1], ${JSON.stringify(PASS)});
    return inputs.length;
  })()`)
  console.log('3. 已填入账号密码')

  // 4) 提交
  const clicked = await evaluate(`(() => {
    const btns = [...document.querySelectorAll('.app-dialog .el-dialog__footer button')];
    const btn = btns.find(b => b.textContent.replace(/\\s/g, '') === '登录');
    if (!btn) return 'no-button:' + btns.map(b => b.textContent.trim()).join('|');
    btn.click();
    return 'clicked';
  })()`)
  console.log('4. 提交按钮：', clicked)

  // 5) 等角色文案变化
  let roleLabel = ''
  for (let i = 0; i < 25; i++) {
    await sleep(400)
    roleLabel = await evaluate(`document.querySelector('.user-role')?.textContent.trim() || ''`)
    if (roleLabel === '管理员') break
  }
  console.log('5. 登录后角色文案：', roleLabel)
  // 键名是 frontend/src/api/client.js 里的 ADMIN_TOKEN_KEY，不是 admin_token
  const token = await evaluate(`localStorage.getItem('jc_admin_token') || ''`)
  console.log('   localStorage 有 token：', !!token, token ? `(${token.length} 字符)` : '')

  // 6) 模型选择器里的管理员专属模型
  const models = await evaluate(`(() => {
    const el = document.querySelector('.model-select, .model-picker, .el-select');
    return el ? el.textContent.trim().slice(0, 80) : 'no-select';
  })()`)
  console.log('6. 模型选择器文本：', models)

  // 7) 刷新后仍为管理员（token 来自 localStorage + AUTH_SECRET 固定）
  await send('Page.navigate', { url: BASE })
  for (let i = 0; i < 30; i++) {
    await sleep(600)
    if (await evaluate(`!!document.querySelector('.user-role')`)) break
  }
  await sleep(1500)
  console.log('7. 刷新后角色文案：', await evaluate(`document.querySelector('.user-role')?.textContent.trim()`))

  const shot = await send('Page.captureScreenshot', { format: 'png' })
  fs.writeFileSync(SHOT, Buffer.from(shot.data, 'base64'))
  console.log('截图：', SHOT)

  ws.close()
  await httpJson(`${CDP}/json/close/${target.id}`)
}

main().catch((err) => {
  console.error('失败：', err.message)
  process.exit(1)
})
