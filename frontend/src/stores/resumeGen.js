import { defineStore } from 'pinia'
import { resumeApi, notifyError } from '../api'
import { useConversationStore } from './conversation'

// 自动生成的简历。
// P2 起结果落库（resume_generations），store 只持有"当前打开的那一版"，
// 历史版本按需从 /resume/generations/{id} 拉取——刷新页面后还能回到同一版。
export const useResumeGenStore = defineStore('resumeGen', {
  state: () => ({
    current: null, // 当前打开的 generation 详情（含 html / data / warnings）
    // 版本列表按会话缓存：侧栏树要在多个会话之间展开，单靠一个 current 会串数据
    lists: {}, // {conversation_id: [{generation_id, version, ...}]}
    loadingLists: {},
    conversationId: '',
    loading: false, // 生成中（LLM，15–20s）
    opening: false, // 打开某一版
    regenerating: false, // 补字段重渲染（0 次 LLM）
    // 示例模板（「先看看模板」）单独一条状态，绝不塞进 current：
    // current 是"已落库的某一版"，塞进去后 regenerate() 会拿一个没有 generation_id
    // 的对象去请求 /resume/generations//regenerate。
    templateHtml: '',
    templateNote: '',
    templateLoading: false,
  }),
  getters: {
    // 保留 result 这个别名：主区视图大量引用 gen.result
    result: (s) => s.current,
    html: (s) => s.current?.html || '',
    warnings: (s) => s.current?.warnings || [],
    missing: (s) => s.current?.missing || [],
    hasResult: (s) => !!s.current?.html,
    // 预览区实际渲染的 HTML：有生成结果优先，否则退到示例模板
    displayHtml: (s) => s.current?.html || s.templateHtml,
    showingTemplate: (s) => !s.current?.html && !!s.templateHtml,
    version: (s) => s.current?.version || 0,
    generationId: (s) => s.current?.generation_id || '',
    list: (s) => s.lists[s.conversationId] || [],
    loadingList: (s) => !!s.loadingLists[s.conversationId],
  },
  actions: {
    listOf(cid) {
      return this.lists[cid] || []
    },
    async generate(cid, extraFields) {
      if (!cid) throw new Error('请先选择会话')
      this.loading = true
      this.conversationId = cid
      try {
        const res = await resumeApi.generate({
          conversation_id: cid,
          extra_fields: extraFields || {},
        })
        this.current = res.data
        await this.loadList(cid)
        // 生成接口同时写了两条会话消息，这里把消息列表补齐但不切换视图
        await useConversationStore().refreshMessages()
        return res.data
      } catch (err) {
        // 保留上一次的预览，避免生成失败后主区突然空掉
        notifyError(err, '简历生成失败')
        throw err
      } finally {
        this.loading = false
      }
    },

    async loadTemplate() {
      // 示例只读、可反复拉取（0.2s 级，无 LLM 无落库），失败不弹窗打扰：
      // 这只影响"看一眼长什么样"的辅助入口。
      if (this.templateHtml) return this.templateHtml
      this.templateLoading = true
      try {
        const res = await resumeApi.template()
        this.templateHtml = res.data?.html || ''
        this.templateNote = res.data?.note || ''
        return this.templateHtml
      } catch {
        return ''
      } finally {
        this.templateLoading = false
      }
    },

    clearTemplate() {
      this.templateHtml = ''
      this.templateNote = ''
    },

    // 「先看看模板」的完整动作：拉示例 + 清掉当前打开的版本。
    // 不清 current 的话，预览区的 displayHtml 会优先渲染上一版简历，
    // 用户点了按钮却看到旧内容，会以为按钮没生效。
    // current 清掉不影响历史版本：侧栏树照样能点回来。
    async showTemplate() {
      await this.loadTemplate()
      this.current = null
    },

    async open(generationId) {
      if (!generationId) {
        this.current = null
        return null
      }
      if (this.current?.generation_id === generationId && this.current?.html) return this.current
      this.opening = true
      try {
        const res = await resumeApi.generationDetail(generationId)
        this.current = res.data
        // 从侧栏树跨会话点进来时，store 里还没有这个会话的上下文，
        // 补上并顺带把版本列表加载好（否则"重新生成一版"会落错会话）
        if (res.data.conversation_id && res.data.conversation_id !== this.conversationId) {
          this.conversationId = res.data.conversation_id
          await this.loadList(this.conversationId)
        }
        return res.data
      } catch (err) {
        notifyError(err, '打开简历失败')
        this.current = null
        throw err
      } finally {
        this.opening = false
      }
    },

    async loadList(cid) {
      if (!cid) return []
      this.loadingLists[cid] = true
      try {
        const res = await resumeApi.generations(cid)
        const items = res.data?.generations || []
        this.lists[cid] = items
        return items
      } catch (err) {
        // 侧栏树与预览都还能用，列表失败不弹窗打扰
        this.lists[cid] = []
        return []
      } finally {
        this.loadingLists[cid] = false
      }
    },

    async regenerate(extraFields) {
      const id = this.generationId
      if (!id) throw new Error('没有可重渲染的版本')
      this.regenerating = true
      try {
        const res = await resumeApi.regenerate(id, { extra_fields: extraFields || {} })
        this.current = res.data
        await this.loadList(this.conversationId || res.data.conversation_id)
        await useConversationStore().refreshMessages()
        return res.data
      } catch (err) {
        notifyError(err, '重新渲染失败')
        throw err
      } finally {
        this.regenerating = false
      }
    },

    reset() {
      this.current = null
      this.lists = {}
      this.loadingLists = {}
      this.conversationId = ''
      this.templateHtml = ''
      this.templateNote = ''
    },

    // 切换会话时清掉"上一份预览"，但不能顺手把新会话刚打开的版本也清掉：
    // 从侧栏树点另一会话的简历节点时，watch 顺序不保证，所以只比会话。
    resetIfOtherConversation(cid) {
      if (this.conversationId && this.conversationId !== cid) {
        this.current = null
        this.conversationId = ''
      }
    },
  },
})
