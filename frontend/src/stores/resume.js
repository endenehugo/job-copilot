import { defineStore } from 'pinia'
import { resumeApi, notifyError } from '../api'
import { useConversationStore } from './conversation'

export const useResumeStore = defineStore('resume', {
  state: () => ({
    versions: [],
    selectedVersionId: '',
    versionDetail: null,
    compare: null, // {score_history, trend}
  }),
  actions: {
    async load(cid) {
      this.versions = []
      this.selectedVersionId = ''
      this.versionDetail = null
      this.compare = null
      if (!cid) return
      try {
        const res = await resumeApi.versions(cid)
        this.versions = res.data.versions
      } catch (err) {
        // 清空成"没有版本"会把服务故障伪装成正常空态
        this.versions = []
        notifyError(err, '简历版本加载失败')
      }
    },
    async selectVersion(versionId) {
      this.selectedVersionId = versionId
      if (!versionId) {
        this.versionDetail = null
        return
      }
      const res = await resumeApi.versionDetail(versionId)
      this.versionDetail = res.data
    },
    async loadCompare(cid) {
      const res = await resumeApi.compare(cid)
      this.compare = res.data
    },
  },
})
