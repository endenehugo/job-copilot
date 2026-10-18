import { defineStore } from 'pinia'
import { jobApi, resumeApi, notifyError } from '../api'
import { useConversationStore } from './conversation'
import { useModelStore } from './model'
import { useRewriteStore } from './rewrite'

export const useAnalysisStore = defineStore('analysis', {
  state: () => ({
    jdText: '',
    // jdText 是跨会话共享的输入框：记住它属于哪个会话，
    // 否则在 A 会话粘的 JD 会被 B 会话的"开始面试"当成本次 JD 用掉
    jdTextCid: '',
    // cid -> JD 草稿：切会话再切回来不丢已粘贴的 JD
    jdDrafts: {},
    analyzing: false,
    analysis: null, // {jd_analysis, scoring, analysis_id?} 或 detail 的扁平结构
    // 会话树子节点数据：conversation_id -> 分析摘要列表（懒加载）
    listByCid: {},
    loadedCids: [],
    loadingList: {},
    rewriteText: '',
    rewriting: false,
    rewrite: null, // {original_issues, improved_version, python_backend_version, agent_version, rewrite_id?}
  }),
  getters: {
    // 某会话可用的 JD 草稿（面试发起时用）。
    // jdText 是跨会话共享的输入框内容，所以要先判断归属：
    // jdTextCid 为空表示粘贴时还没有会话（空态），这种草稿对任何会话都可用——
    // 早先直接要求 cid 相等，会把"空态先粘 JD、再上传简历"的草稿判成没有 JD。
    jdDraftFor: (s) => (cid) => {
      const text = (s.jdText || '').trim()
      if (!text) return ''
      return !s.jdTextCid || s.jdTextCid === cid ? text : ''
    },
  },
  actions: {
    // 输入框统一入口：写入内容并记录它属于哪个会话
    setJdText(cid, text) {
      const key = cid || ''
      this.jdText = text ?? ''
      this.jdTextCid = key
      this.jdDrafts[key] = this.jdText
    },
    // 打开弹窗 / 切会话时把输入框切到该会话的草稿。
    // 该会话没粘过时退回"还没有会话时粘的那份"——空态先粘 JD、再上传简历是常见顺序。
    loadJdDraft(cid) {
      const key = cid || ''
      const own = this.jdDrafts[key]
      this.jdText = own !== undefined ? own : this.jdDrafts[''] || ''
      this.jdTextCid = key
    },
    // ---- 会话树子节点（懒加载） ----
    async loadList(cid, force = false) {
      if (!cid) return
      if (!force && this.loadedCids.includes(cid)) return
      this.loadingList[cid] = true
      try {
        const res = await jobApi.list(cid)
        this.listByCid[cid] = res.data.analyses
        if (!this.loadedCids.includes(cid)) this.loadedCids.push(cid)
      } catch (err) {
        // 静默清空会让用户把"加载失败"当成"从来没有分析过"
        this.listByCid[cid] = []
        notifyError(err, '分析历史加载失败')
      } finally {
        this.loadingList[cid] = false
      }
    },
    listOf(cid) {
      return this.listByCid[cid] || []
    },
    // 点击分析子节点：主区展示该条全量（detail 与 latest 同构，复用渲染）
    async openNode(cid, node) {
      const conv = useConversationStore()
      conv.selectNode('analysis', cid, node.id)
      const res = await jobApi.detail(node.id)
      this.analysis = res.data
    },

    // ---- 发起分析 ----
    async loadLatest(cid) {
      this.analysis = null
      if (!cid) return
      try {
        const res = await jobApi.latest(cid)
        this.analysis = res.data
      } catch (err) {
        // 404 = 该会话还没分析过，是正常空态；其他失败必须提示，
        // 否则"加载失败"和"没分析过"在界面上长得一模一样
        if (err.code !== 404) notifyError(err, '历史分析加载失败')
      }
    },
    async analyze() {
      const conv = useConversationStore()
      const cid = conv.currentId
      if (!this.jdText.trim()) throw new Error('请输入 JD 文本')
      this.analyzing = true
      try {
        const res = await jobApi.analyze(useModelStore().payloadWithModel({ conversation_id: cid, jd_text: this.jdText }))
        this.analysis = res.data
        // 新分析进主区视图，并刷新树与徽标
        conv.selectNode('analysis', cid, res.data.analysis_id)
        await Promise.all([this.loadList(cid, true), conv.loadList().catch(() => {})])
      } finally {
        this.analyzing = false
      }
    },
    async analyzeScreenshot(imageUrl) {
      const conv = useConversationStore()
      const cid = conv.currentId
      this.analyzing = true
      try {
        const res = await jobApi.analyzeScreenshot(useModelStore().payloadWithModel({ conversation_id: cid, image_url: imageUrl }))
        this.analysis = res.data
        conv.selectNode('analysis', cid, res.data.analysis_id)
        await Promise.all([this.loadList(cid, true), conv.loadList().catch(() => {})])
      } finally {
        this.analyzing = false
      }
    },

    // ---- 删除单条 ----
    async remove(cid, analysisId) {
      await jobApi.remove(analysisId)
      const conv = useConversationStore()
      this.listByCid[cid] = (this.listByCid[cid] || []).filter((a) => a.id !== analysisId)
      if (conv.activeNodeId === analysisId) {
        this.analysis = null
        await conv.backToConversation()
      }
      conv.loadList().catch(() => {})
    },

    // ---- 项目优化（保留在 analysis store：与旧版共享文本输入状态） ----
    async rewrite() {
      const conv = useConversationStore()
      const cid = conv.currentId
      if (!this.rewriteText.trim()) throw new Error('请输入项目描述')
      this.rewriting = true
      try {
        const res = await resumeApi.rewrite(
          useModelStore().payloadWithModel({
            conversation_id: cid,
            project_description: this.rewriteText,
          })
        )
        this.rewrite = res.data
        // 新优化进主区视图，并刷新树与徽标
        conv.selectNode('rewrite', cid, res.data.rewrite_id)
        await Promise.all([
          useRewriteStore().loadList(cid, true),
          conv.loadList().catch(() => {}),
        ])
      } finally {
        this.rewriting = false
      }
    },
    closeAnalysis() {
      this.analysis = null
    },
    closeRewrite() {
      this.rewrite = null
    },
  },
})
