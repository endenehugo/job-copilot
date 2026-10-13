import { defineStore } from 'pinia'
import { jobApi, resumeApi, notifyError } from '../api'
import { useConversationStore } from './conversation'
import { useRewriteStore } from './rewrite'

export const useAnalysisStore = defineStore('analysis', {
  state: () => ({
    jdText: '',
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
  actions: {
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
        const res = await jobApi.analyze({ conversation_id: cid, jd_text: this.jdText })
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
        const res = await jobApi.analyzeScreenshot({ conversation_id: cid, image_url: imageUrl })
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
        const res = await resumeApi.rewrite({
          conversation_id: cid,
          project_description: this.rewriteText,
        })
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
