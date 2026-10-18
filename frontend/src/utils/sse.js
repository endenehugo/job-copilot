// SSE 读取工具：对话与分析两条流式链路共用。
// axios 拿不到逐步返回的响应体，所以流式端点直接用原生 fetch + ReadableStream。

export function parseSse(raw) {
  let event = 'message'
  let data = ''
  for (const line of raw.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) data += line.slice(5).trim()
  }
  if (!data) return null
  try {
    return { event, data: JSON.parse(data) }
  } catch {
    return null
  }
}

// 发一个 POST 并按事件回调。error 事件统一转成异常抛给调用方，
// 这样组件层只需要 try/catch，不用自己判断流里有没有 error。
export async function postSse(path, body, onEvent, { signal } = {}) {
  // 原生 fetch 不走 axios 拦截器，管理员 token 要手动带（流式端点按角色校验模型白名单）
  const adminToken = localStorage.getItem('jc_admin_token')
  const resp = await fetch(`/api/v1${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(adminToken ? { 'X-Admin-Token': adminToken } : {}),
    },
    body: JSON.stringify(body),
    signal,
  })

  if (!resp.ok || !resp.body) {
    // 前置校验失败时后端返回的是普通 JSON 信封，不是 SSE，别让用户看到 "HTTP 400"
    let message = `请求失败（HTTP ${resp.status}）`
    try {
      const data = await resp.json()
      message = data?.message || data?.detail || message
    } catch {
      /* 响应体不是 JSON 就沿用默认文案 */
    }
    throw new Error(message)
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let streamError = ''

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let idx
    while ((idx = buffer.indexOf('\n\n')) >= 0) {
      const raw = buffer.slice(0, idx)
      buffer = buffer.slice(idx + 2)
      const event = parseSse(raw)
      if (!event) continue
      if (event.event === 'error') {
        streamError = event.data?.message || '请求失败'
        continue
      }
      onEvent?.(event.event, event.data)
    }
  }

  if (streamError) throw new Error(streamError)
}
