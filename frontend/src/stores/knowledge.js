import { defineStore } from 'pinia'
import { knowledgeApi } from '../api'

export const useKnowledgeStore = defineStore('knowledge', {
  state: () => ({
    query: '',
    results: [],
    searching: false,
    rebuilding: false,
    status: null,
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
    async search() {
      if (!this.query.trim()) throw new Error('请输入搜索内容')
      this.searching = true
      try {
        const res = await knowledgeApi.query(this.query, 4)
        this.results = res.data.results
      } finally {
        this.searching = false
      }
    },
  },
})
