import { defineStore } from 'pinia'
import { knowledgeApi, notifyError } from '../api'

export const useKnowledgeStore = defineStore('knowledge', {
  state: () => ({
    rebuilding: false,
    status: null,
    newContent: '',
    submitting: false,
    reviewResult: null, // {approved, review}
    expanding: false,
    expandResult: null, // {added, rejected}
  }),
  actions: {
    async loadStatus() {
      try {
        const res = await knowledgeApi.status()
        this.status = res.data
      } catch (err) {
        // 状态置空会让面板显示成"未建索引"，把服务故障伪装成正常状态
        this.status = null
        notifyError(err, '知识库状态加载失败')
      }
    },
    async rebuild() {
      this.rebuilding = true
      try {
        await knowledgeApi.rebuild()
        await this.loadStatus()
      } finally {
        this.rebuilding = false
      }
    },
    async submitContent() {
      if (!this.newContent.trim()) throw new Error('请输入要贡献的内容')
      this.submitting = true
      try {
        const res = await knowledgeApi.submit(this.newContent)
        this.reviewResult = { approved: res.data.approved, review: res.data.review }
        if (res.data.approved) this.newContent = ''
        await this.loadStatus()
      } finally {
        this.submitting = false
      }
    },
    async selfExpand() {
      this.expanding = true
      try {
        const res = await knowledgeApi.selfExpand(3)
        this.expandResult = res.data
        await this.loadStatus()
      } finally {
        this.expanding = false
      }
    },
  },
})
