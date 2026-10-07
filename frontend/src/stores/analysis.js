import { defineStore } from 'pinia'
import { jobApi, resumeApi } from '../api'
import { useConversationStore } from './conversation'

export const useAnalysisStore = defineStore('analysis', {
  state: () => ({
    jdText: '',
    analyzing: false,
    analysis: null, // {jd_analysis, scoring, extracted_jd_preview?}
    rewriteText: '',
    rewriting: false,
    rewrite: null, // {original_issues, improved_version, python_backend_version, agent_version}
  }),
  actions: {
    async loadLatest(cid) {
      this.analysis = null
      if (!cid) return
      try {
        const res = await jobApi.latest(cid)
        this.analysis = res.data
      } catch {
        // 暂无分析记录，保持为空
      }
    },
    async analyze() {
      const cid = useConversationStore().currentId
      if (!this.jdText.trim()) throw new Error('请输入 JD 文本')
      this.analyzing = true
      try {
        const res = await jobApi.analyze({ conversation_id: cid, jd_text: this.jdText })
        this.analysis = res.data
      } finally {
        this.analyzing = false
      }
    },
    async analyzeScreenshot(imageUrl) {
      const cid = useConversationStore().currentId
      this.analyzing = true
      try {
        const res = await jobApi.analyzeScreenshot({ conversation_id: cid, image_url: imageUrl })
        this.analysis = res.data
      } finally {
        this.analyzing = false
      }
    },
    async rewrite() {
      const cid = useConversationStore().currentId
      if (!this.rewriteText.trim()) throw new Error('请输入项目描述')
      this.rewriting = true
      try {
        const res = await resumeApi.rewrite({
          conversation_id: cid,
          project_description: this.rewriteText,
        })
        this.rewrite = res.data
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
