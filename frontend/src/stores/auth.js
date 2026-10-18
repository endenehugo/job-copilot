import { defineStore } from 'pinia'
import { authApi, ADMIN_TOKEN_KEY } from '../api'

// /auth/me 只回 role 不回用户名，刷新后要靠本地记住上一次登录的名字
const USERNAME_KEY = 'jc_admin_username'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: localStorage.getItem(ADMIN_TOKEN_KEY) || '',
    role: 'user',
    username: '',
  }),
  getters: {
    isAdmin: (s) => s.role === 'admin',
    displayName: (s) => s.username || (s.role === 'admin' ? '管理员' : '本地访客'),
  },
  actions: {
    // 应用启动时恢复登录态：token 失效（过期/后端换密钥）则降级为普通用户
    async restore() {
      if (!this.token) {
        this.role = 'user'
        return
      }
      try {
        const res = await authApi.me()
        this.role = res.data.role
        if (this.role !== 'admin') this.clearToken()
        else this.username = localStorage.getItem(USERNAME_KEY) || '管理员'
      } catch {
        // 网络异常不轻易清 token，下次再验
        this.role = 'user'
      }
    },
    async login(username, password) {
      const res = await authApi.login({ username, password })
      this.token = res.data.token
      this.role = res.data.role
      this.username = res.data.username
      localStorage.setItem(ADMIN_TOKEN_KEY, this.token)
      localStorage.setItem(USERNAME_KEY, this.username || username)
      return res.data
    },
    logout() {
      this.clearToken()
      this.role = 'user'
      this.username = ''
    },
    clearToken() {
      this.token = ''
      localStorage.removeItem(ADMIN_TOKEN_KEY)
      localStorage.removeItem(USERNAME_KEY)
    },
  },
})
