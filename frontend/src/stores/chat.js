import { reactive } from 'vue'
import { defineStore } from 'pinia'
import { conversationApi, documentApi } from '../api'
import { parseSse } from '../utils/sse'
import { useConversationStore } from './conversation'
import { useModelStore } from './model'

export const useChatStore = defineStore('chat', {
  state: () => ({
    messages: [],
    pendingImages: [], // {url}
    // 本次在输入框里上传的简历文档：{key,name,status,documentId}
    // 文档按会话建索引（不是按消息），所以发送后不再当作"待发附件"
    pendingDocs: [],
    // 已回放过的会话 id：用于区分"切换会话"与"同会话刷新 detail"，
    // 后者（如上传文档后刷新资料库）不能清空用户已选的待发图片
    loadedConversationId: '',
    sending: false,
    streamController: null, // AbortController，用于"停止生成"与会话切换时中断接收
    // 外部视图（如 JD 分析卡片）请求输入框预置技能与草稿：
    // { skill: 'job-match', text: '...' }。Composer 消费后置空，避免切视图回来又灌一遍
    composerPreset: null,
  }),
  actions: {
    presetComposer(preset) {
      this.composerPreset = preset || null
    },
    stopStreaming() {
      // 只中断前端接收；后端会继续生成并落库，之后切回该会话能看到完整回答
      this.streamController?.abort()
      this.streamController = null
      // sending 必须在这里收尾：abort 之后 done 事件不会再来，而 sendStream 的
      // finally 要靠 controller 身份判断，凭证已被清掉 → 判断恒为假 → sending
      // 永远停在 true，整个页面再也发不出消息（切会话、点"新对话"、点"停止生成"
      // 都会走到这里）。
      this.sending = false
    },
    loadFromDetail(detail) {
      const cid = useConversationStore().currentId || ''
      const switched = cid !== this.loadedConversationId
      // meta 是消息的结构化载荷（简历卡片等）。这里只挑 role/content 会把卡片信息丢掉，
      // 消息流就只剩一句"已生成，见下方卡片"却看不到卡片。
      const messages = (detail?.messages || []).map((m) => ({
        role: m.role,
        content: m.content,
        ...(m.meta ? { meta: m.meta } : {}),
      }))
      if (switched) {
        this.messages = messages
        this.pendingImages = []
        this.pendingDocs = []
        this.loadedConversationId = cid
        return
      }
      // 同会话内的 detail 刷新（上传文档后同步资料库）：不覆盖正在流式生成的回答
      if (this.sending) return
      this.messages = messages
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
    // 简历/文档：落在会话级资料库（解析 + 建索引），上传完成后即可被对话检索
    async uploadDoc(file) {
      const cid = useConversationStore().currentId
      const entry = reactive({
        key: `doc_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
        name: file.name,
        status: 'uploading',
        documentId: '',
      })
      this.pendingDocs.push(entry)
      const form = new FormData()
      form.append('conversation_id', cid)
      form.append('file', file)
      try {
        const res = await documentApi.upload(form)
        const doc = res.data?.document || {}
        entry.documentId = doc.document_id || ''
        entry.status = doc.status || 'indexed'
        return entry
      } catch (err) {
        // 失败项保留在输入框上方，否则用户会以为文件已经进了资料库
        entry.status = 'failed'
        throw err
      }
    },
    dismissDoc(index) {
      this.pendingDocs.splice(index, 1)
    },
    async send(query, options = {}) {
      const conv = useConversationStore()
      const images = this.pendingImages.map((img) => img.url)
      if (!query.trim() && images.length === 0) {
        throw new Error('请输入问题或上传图片')
      }
      // 防连点：置位必须在任何 await 之前，否则 create 的数秒窗口内可重复发送
      if (this.sending) return
      this.sending = true
      try {
        if (!conv.currentId) await conv.create()

        // 用发送前捕获的 images 组内容：create() 可能触发 detail 回放清空 pendingImages
        const userContent = [
          ...images.map((url) => `![image](${url})`),
          query.trim(),
        ]
          .filter(Boolean)
          .join('\n')

        this.messages.push({ role: 'user', content: userContent })
        this.pendingImages = []
        this.pendingDocs = this.pendingDocs.filter((d) => d.status === 'failed')

        const res = await conversationApi.chat(
          useModelStore().payloadWithModel({
            conversation_id: conv.currentId,
            query,
            mode: 'agent',
            image_urls: images,
            skill: options.skill || '',
            skill_option: options.skillOption || '',
          })
        )
        this.messages.push({
          role: 'assistant',
          content: res.data.answer,
          sources: res.data.sources,
          verification: res.data.verification,
          files: res.data.files || null,
        })
      } catch (err) {
        this.messages.push({ role: 'assistant', content: `**出错了**：${err.message}` })
      } finally {
        this.sending = false
      }
      // 同步会话标题/预览：不阻塞下一次发送；失败不影响刚收到的回答
      conv.loadList().catch(() => {})
    },
    async sendStream(query, options = {}) {
      const conv = useConversationStore()
      const images = this.pendingImages.map((img) => img.url)
      if (!query.trim() && images.length === 0) {
        throw new Error('请输入问题或上传图片')
      }
      // 防连点：置位必须在 await conv.create() 之前
      if (this.sending) return
      this.sending = true

      // reactive 包裹后，流式追加才能实时驱动视图更新
      const assistantMsg = reactive({
        role: 'assistant',
        content: '',
        // 模型的思考过程（reasoning_content）。只在前端内存里，不进 messages、
        // 不落库——一次长文档任务能产生上万 token 的思考，写进历史会让下一轮
        // 输入直接翻倍。刷新页面就没了，这是有意的。
        reasoning: '',
        sources: null,
        verification: null,
        files: null,
        streaming: true,
        // 计时起点：工具轮是非流式 invoke，实测静默 60~100s，
        // 期间没有 delta 可显示，由前端自己算已等待秒数，别让用户以为卡死
        startedAt: Date.now(),
      })

      // 提到 try 外：finally 里要靠它判断"这条流是不是还是当前那条"
      let controller = null

      try {
        if (!conv.currentId) await conv.create()

        // 同 send()：用捕获的 images，避免 create() 回放 detail 时清空待发图片
        const userContent = [
          ...images.map((url) => `![image](${url})`),
          query.trim(),
        ]
          .filter(Boolean)
          .join('\n')

        this.messages.push({ role: 'user', content: userContent })
        this.pendingImages = []
        this.pendingDocs = this.pendingDocs.filter((d) => d.status === 'failed')
        this.messages.push(assistantMsg)

        controller = new AbortController()
        this.streamController = controller
        // 原生 fetch 不走 axios 拦截器，管理员 token 需手动带上，
        // 否则流式端点按普通用户白名单校验模型，管理员的选型会被 400
        const adminToken = localStorage.getItem('jc_admin_token')
        const resp = await fetch('/api/v1/conversation/chat/stream', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...(adminToken ? { 'X-Admin-Token': adminToken } : {}),
          },
          body: JSON.stringify(
            useModelStore().payloadWithModel({
              conversation_id: conv.currentId,
              query,
              mode: 'agent',
              image_urls: images,
              skill: options.skill || '',
              skill_option: options.skillOption || '',
            })
          ),
          signal: controller.signal,
        })
        if (!resp.ok || !resp.body) {
          // 中间件层拒绝（限流）回的是真实 HTTP 4xx + 统一信封 JSON：
          // 把信封里的中文 message 带出来，别只给一个 "HTTP 429"
          let message = `HTTP ${resp.status}`
          try {
            const body = await resp.json()
            if (body?.message) message = body.message
          } catch {
            // 非 JSON 响应，保留状态码文案
          }
          throw new Error(message)
        }
        // 流被后端在入口拒绝时（如每日配额熔断），返回的是统一信封 JSON 而不是
        // SSE——不拦住的话会走下面的 SSE 解析，一个事件都收不到，气泡空白收场
        const contentType = resp.headers.get('content-type') || ''
        if (!contentType.includes('text/event-stream')) {
          let message = '服务返回了意外的响应格式，请稍后重试'
          let code = ''
          try {
            const body = await resp.json()
            if (body?.message) message = body.message
            code = body?.code ?? ''
          } catch {
            // 保留默认文案
          }
          const err = new Error(message)
          err.code = code
          throw err
        }

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
            } else if (event.event === 'thinking_delta') {
              // 思考过程：只累加用于展示，绝不并进 content——否则会被当成回答
              // 存进历史，也会污染 done 里的最终内容
              assistantMsg.reasoning += event.data.text || ''
            } else if (event.event === 'tool_status') {
              // agent 工具链进度：替换式更新，气泡内显示当前正在执行的工具
              assistantMsg.toolStatus = event.data
            } else if (event.event === 'thinking') {
              // 工具轮开始生成工具参数（非流式调用，最长静默 100s）。
              // 这里先占位，配合 MessageItem 的计时器显示"已等待 Ns"。
              assistantMsg.toolStatus = { ...event.data, thinking: true }
            } else if (event.event === 'sources') {
              assistantMsg.sources = event.data.sources
            } else if (event.event === 'verification') {
              assistantMsg.verification = event.data.verification
              // 引用标注依赖完整回答，后端在 done 之后补发，这里覆盖为带标注版本
              if (event.data.cited_answer) assistantMsg.content = event.data.cited_answer
            } else if (event.event === 'files') {
              assistantMsg.files = event.data.files
            } else if (event.event === 'done') {
              assistantMsg.content = event.data.answer || assistantMsg.content
              if (event.data.files) assistantMsg.files = event.data.files
              if (event.data.warnings && event.data.warnings.length) {
                // 工具链异常结束（轮次耗尽/工具失败）：气泡内提示，不当成正常回答
                assistantMsg.degraded = true
                assistantMsg.warnings = event.data.warnings
              }
              assistantMsg.streaming = false
              // 回答已经完整下发，后续只有 done 之后的 verification（要再跑一次 LLM，
              // 实测 5~33 秒）。那只是给已有内容补引用标注，不该继续按住输入框：
              // 用户在气泡里看到答案后想追问，被锁着几十秒会以为页面卡死。
              // 解锁只在这里做，finally 里用控制器身份兜住并发场景。
              // 这里同样要判身份：旧流的 done 若在用户已经发了新消息之后才到达，
              // 无条件置 false 会把新请求的发送锁解开。
              if (this.streamController === controller) this.sending = false
            } else if (event.event === 'error') {
              streamError = event.data.message
            }
          }
        }

        assistantMsg.streaming = false
        if (streamError) {
          // 已有部分内容时保留，不要用错误提示覆盖掉已生成的部分
          if (assistantMsg.content.trim()) {
            assistantMsg.content += `\n\n**注意**：${streamError}`
          } else {
            assistantMsg.content = `**出错了**：${streamError}`
          }
        }
        // 会话列表（标题/预览）非阻塞刷新：之前在 try 内 await，失败会被 catch
        // 误当成流式错误，把已生成的回答覆盖成"出错了"
        conv.loadList().catch(() => {})
      } catch (err) {
        assistantMsg.streaming = false
        // 主动中断（stopStreaming）不是错误：这时气泡可能已被新会话的 detail 替换，
        // 按原来的"气泡不在列表里就抛出"会把 AbortError 当异常弹给用户。
        if (err.name !== 'AbortError' && !this.messages.includes(assistantMsg)) {
          // 建会话阶段就失败、气泡尚未上屏：交还调用方提示（Composer 的 notifyError）
          throw err
        }
        if (err.name === 'AbortError') {
          // 用户主动停止：不算错误，保留已生成的部分
          if (assistantMsg.content.trim()) {
            assistantMsg.content += '\n\n*(已停止生成，后端仍会生成完整回答并存入历史)*'
          }
        } else if (!assistantMsg.content) {
          assistantMsg.content = `**出错了**：${err.message}`
        }
      } finally {
        // 只在"当前流仍是自己"时收尾：done 已经提前解锁，用户可能在上一条流
        // 补引用标注期间就发了新消息，那时 sending 属于新请求，不能被旧流清掉。
        if (this.streamController === controller) {
          this.streamController = null
          this.sending = false
        }
      }
    },
  },
})
