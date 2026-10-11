import { defineStore } from 'pinia'
import { conversationApi } from '../api'

export const useConversationStore = defineStore('conversation', {
  state: () => ({
    conversations: [],
    currentId: '',
    detail: null,
    loading: false,
    // select 请求序号：快速点 A→B 时丢弃 A 的过期响应，避免"高亮 B、内容 A"
    selectSeq: 0,
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
      // 列表刷新与详情加载互不依赖，串行会多等一个往返
      await Promise.all([this.loadList(), this.select(res.data.conversation_id)])
      return res.data.conversation_id
    },
    async select(id) {
      const seq = ++this.selectSeq
      this.loading = true
      this.currentId = id
      this.detail = null
      try {
        const res = await conversationApi.detail(id)
        if (seq !== this.selectSeq) {
          // 过期响应：期间用户已切到别的会话，detail 由最新请求负责赋值
          return null
        }
        this.detail = res.data
        return res.data
      } finally {
        if (seq === this.selectSeq) this.loading = false
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
