import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus, { ElMessage } from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'

import App from './App.vue'
import router from './router'
import './style.css'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.use(ElementPlus, { locale: zhCn })

// B23：渲染期/异步异常全局兜底，之前这些错误只在 console 消失（或干脆不出现）。
// 同一错误 10 秒内只弹一次，避免轮询类错误刷屏。
const seenAt = new Map()
function reportError(kind, err) {
  const msg = err?.message || String(err)
  console.error(`[${kind}]`, err)
  const now = Date.now()
  if (now - (seenAt.get(msg) || 0) < 10_000) return
  seenAt.set(msg, now)
  ElMessage.error(`页面异常：${msg}`)
}
app.config.errorHandler = (err, _instance, info) => reportError(`渲染异常 ${info}`, err)
window.addEventListener('unhandledrejection', (e) => reportError('异步异常', e.reason))

app.mount('#app')
