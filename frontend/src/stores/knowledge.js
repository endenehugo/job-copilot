import { defineStore } from 'pinia'
import { knowledgeApi } from '../api'

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
      } catch {
        this.status = null
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
