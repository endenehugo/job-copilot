import { defineStore } from 'pinia'
import { modelsApi } from '../api'
import { useAuthStore } from './auth'

// 当前模型按角色分别记忆（普通用户与管理员的清单不同）
const CURRENT_KEY = 'jc_model_current'

function readSaved() {
  try {
    return JSON.parse(localStorage.getItem(CURRENT_KEY) || '{}')
  } catch {
    return {}
  }
}

export const useModelStore = defineStore('model', {
  state: () => ({
    models: [],
    default: '',
    current: '',
    role: 'user',
    loading: false,
  }),
  actions: {
    // 按当前登录态拉取可选清单；登录/退出后角色变化需重新调用
    async load() {
      this.loading = true
      try {
        const res = await modelsApi.list()
        this.role = res.data.role
        this.models = res.data.models
        this.default = res.data.default
        // 恢复该角色上次的选择；不在清单里（如退出登录后残留管理员模型）则回落默认
        const saved = readSaved()[this.role]
        this.current = this.models.includes(saved) ? saved : this.default
      } finally {
        this.loading = false
      }
    },
    setCurrent(model) {
      if (!this.models.includes(model)) return
      this.current = model
      const saved = readSaved()
      saved[this.role] = model
      localStorage.setItem(CURRENT_KEY, JSON.stringify(saved))
    },
    // 各请求入口统一带 model 字段；显式传入的优先
    payloadWithModel(data = {}) {
      return { ...data, model: data.model ?? this.current }
    },
  },
})
