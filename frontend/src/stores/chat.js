import { reactive } from 'vue'
import { defineStore } from 'pinia'
import { conversationApi } from '../api'
import { useConversationStore } from './conversation'

function parseSse(raw) {
  let event = 'message'
  let data = ''
  for (const line of raw.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) data += line.slice(5).trim()
  }
  if (!data) return null
  try {
    return { event, data: JSON.parse(data) }
  } catch {
    return null
  }
}

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
    async sendStream(query) {
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

      // reactive 包裹后，流式追加才能实时驱动视图更新
      const assistantMsg = reactive({
        role: 'assistant',
        content: '',
        sources: null,
        verification: null,
        streaming: true,
      })
      this.messages.push(assistantMsg)

      try {
        const resp = await fetch('/api/v1/conversation/chat/stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            conversation_id: conv.currentId,
            query,
            mode: 'agent',
            image_urls: images,
          }),
        })
        if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`)

        const reader = resp.body.getReader()
        const decoder = new TextDecoder('utf-8')
        let buffer = ''
        let streamError = null

        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          let idx
          while ((idx = buffer.indexOf('\n\n')) >= 0) {
            const raw = buffer.slice(0, idx)
            buffer = buffer.slice(idx + 2)
            const event = parseSse(raw)
            if (!event) continue
            if (event.event === 'delta') {
              assistantMsg.content += event.data.text || ''
            } else if (event.event === 'sources') {
              assistantMsg.sources = event.data.sources
            } else if (event.event === 'verification') {
              assistantMsg.verification = event.data.verification
            } else if (event.event === 'done') {
              assistantMsg.content = event.data.answer || assistantMsg.content
              assistantMsg.streaming = false
            } else if (event.event === 'error') {
              streamError = event.data.message
            }
          }
        }

        assistantMsg.streaming = false
        if (streamError) assistantMsg.content = `**出错了**：${streamError}`
        await conv.loadList()
      } catch (err) {
        assistantMsg.streaming = false
        if (!assistantMsg.content) {
          assistantMsg.content = `**出错了**：${err.message}`
        }
      } finally {
        this.sending = false
      }
    },
  },
})
