import { defineStore } from 'pinia'
import { conversationApi } from '../api'
import { useConversationStore } from './conversation'

export const useChatStore = defineStore('chat', {
  state: () => ({
    messages: [],
    pendingImages: [], // {url}
    sending: false,
  }),
  actions: {
    loadFromDetail(detail) {
      this.messages = (detail?.messages || []).map((m) => ({ role: m.role, content: m.content }))
      this.pendingImages = []
    },
    async uploadImage(file) {
      const cid = useConversationStore().currentId
      const form = new FormData()
      form.append('conversation_id', cid)
      form.append('file', file)
      const res = await conversationApi.uploadImage(form)
      this.pendingImages.push({ url: res.data.image_url })
    },
    removeImage(index) {
      this.pendingImages.splice(index, 1)
    },
    async send(query) {
      const conv = useConversationStore()
      if (!conv.currentId) await conv.create()
      const images = this.pendingImages.map((img) => img.url)
      if (!query.trim() && images.length === 0) {
        throw new Error('请输入问题或上传图片')
      }

      const userContent = [
        ...this.pendingImages.map((img) => `![image](${img.url})`),
        query.trim(),
      ]
        .filter(Boolean)
        .join('\n')

      this.messages.push({ role: 'user', content: userContent })
      this.pendingImages = []
      this.sending = true
      try {
        const res = await conversationApi.chat({
          conversation_id: conv.currentId,
          query,
          mode: 'agent',
          image_urls: images,
        })
        this.messages.push({
          role: 'assistant',
          content: res.data.answer,
          sources: res.data.sources,
          verification: res.data.verification,
        })
        // 同步会话标题/预览
        await conv.loadList()
      } catch (err) {
        this.messages.push({ role: 'assistant', content: `**出错了**：${err.message}` })
      } finally {
        this.sending = false
      }
    },
  },
})
