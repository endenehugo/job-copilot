import { defineStore } from 'pinia'
import { conversationApi } from '../api'

export const useConversationStore = defineStore('conversation', {
  state: () => ({
    conversations: [],
    currentId: '',
    detail: null,
    loading: false,
  }),
  getters: {
    current: (s) => s.conversations.find((c) => c.conversation_id === s.currentId) || null,
    documents: (s) => s.detail?.documents || [],
  },
  actions: {
    async loadList() {
      const res = await conversationApi.list()
      this.conversations = res.data.conversations
    },
    async create() {
      const res = await conversationApi.create({ title: '新对话', mode: 'agent' })
      await this.loadList()
      await this.select(res.data.conversation_id)
      return res.data.conversation_id
    },
    async select(id) {
      this.loading = true
      this.currentId = id
      this.detail = null
      try {
        const res = await conversationApi.detail(id)
        this.detail = res.data
        return res.data
      } finally {
        this.loading = false
      }
    },
    async refreshDetail() {
      if (this.currentId) await this.select(this.currentId)
    },
    async remove(cid) {
      await conversationApi.remove(cid)
      await this.loadList()
      // 删除的是当前会话：清空主区（Workspace 的 watch 会联动清空各模块）
      if (cid === this.currentId) {
        this.currentId = ''
        this.detail = null
      }
    },
  },
})
