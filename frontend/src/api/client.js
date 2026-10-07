import axios from 'axios'
import { ElMessage } from 'element-plus'

const client = axios.create({ baseURL: '/api/v1', timeout: 300000 })

client.interceptors.response.use(
  (response) => {
    const body = response.data
    if (body && typeof body === 'object' && 'code' in body) {
      // 后端契约：code === 'success' 为成功；错误时 code 为 HTTP 语义整数（400/404/500）
      if (body.code === 'success') return body
      const err = new Error(body.message || '请求失败')
      err.code = body.code
      err.data = body.data
      return Promise.reject(err)
    }
    return Promise.reject(new Error('响应格式异常'))
  },
  (error) => {
    const message = error.response?.data?.message || error.response?.data?.detail || error.message || '网络异常'
    return Promise.reject(new Error(message))
  }
)

export function notifyError(err, fallback = '操作失败') {
  ElMessage.error(err?.message || fallback)
}

export default client
