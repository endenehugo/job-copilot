import axios from 'axios'
import { ElMessage } from 'element-plus'

// 旧代码统一 300 秒：后端挂起或断网时，用户要盯着转圈五分钟，
// 最后收到的还是一句英文原文（timeout of 300000ms exceeded）。
export const DEFAULT_TIMEOUT = 30000
export const LONG_TIMEOUT = 120000
export const BULK_TIMEOUT = 300000

// 管理员登录态的唯一存储键，stores/auth 与本文件共用
export const ADMIN_TOKEN_KEY = 'jc_admin_token'

const client = axios.create({ baseURL: '/api/v1', timeout: DEFAULT_TIMEOUT })
// 上传、LLM 分析、导出等长操作单独放宽
export const longClient = axios.create({ baseURL: '/api/v1', timeout: LONG_TIMEOUT })

// 管理员身份随每个请求携带；无 token 即普通用户，后端按角色做模型白名单
function attachAuth(instance) {
  instance.interceptors.request.use((config) => {
    const token = localStorage.getItem(ADMIN_TOKEN_KEY)
    if (token) config.headers['X-Admin-Token'] = token
    return config
  })
}

export function humanizeError(error) {
  if (!error.response) {
    // 超时与断网都拿不到 response
    if (error.code === 'ECONNABORTED' || /timeout/i.test(error.message || '')) {
      return '服务响应较慢，请稍后重试'
    }
    return '网络连接失败，请检查网络后重试'
  }
  const status = error.response.status
  if (status === 401) return '登录状态已失效，请重新登录'
  if (status === 403) return '没有权限执行该操作'
  if (status === 404) return '请求的资源不存在'
  if (status >= 500) return '服务暂时不可用，请稍后重试'
  return error.response.data?.message || error.response.data?.detail || error.message || '操作失败'
}

// 降级提示：后端在结果可信度受损时（验证没跑成、模型漏字段、重排降级等）带
// degraded/warnings。集中在这里提示，各组件就不必各自判断——否则每加一处降级
// 就要改一个组件，永远补不完。
const degradedShown = new Set()
function warnIfDegraded(body) {
  if (!body.degraded && !body.warnings?.length) return
  const text =
    (body.warnings || []).filter(Boolean).join('；') || '部分功能降级，结果可能不完整'
  // 同一条提示短时间内重复出现会变成轰炸（例如连续多轮对话都降级）
  if (degradedShown.has(text)) return
  degradedShown.add(text)
  setTimeout(() => degradedShown.delete(text), 10000)
  ElMessage({ type: 'warning', message: text, duration: 6000 })
}

function attachInterceptors(instance) {
  instance.interceptors.response.use(
    (response) => {
      const body = response.data
      if (body && typeof body === 'object' && 'code' in body) {
        // 后端契约：code === 'success' 为成功；错误时 code 为 HTTP 语义整数（400/404/500）
        if (body.code === 'success') {
          warnIfDegraded(body)
          return body
        }
        const err = new Error(body.message || '请求失败')
        err.code = body.code
        err.data = body.data
        return Promise.reject(err)
      }
      return Promise.reject(new Error('响应格式异常，请稍后重试'))
    },
    (error) => Promise.reject(new Error(humanizeError(error)))
  )
}

attachInterceptors(client)
attachInterceptors(longClient)
attachAuth(client)
attachAuth(longClient)

export function notifyError(err, fallback = '操作失败') {
  ElMessage.error(err?.message || fallback)
}

export default client
