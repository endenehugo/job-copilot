import { defineStore } from 'pinia'
import { resumeApi, notifyError } from '../api'
import { useConversationStore } from './conversation'

// 项目优化历史（2026-10-13 迁移到会话记录后新增：后端已落库 project_rewrites）
export const useRewriteStore = defineStore('rewrite', {
  state: () => ({
    // 会话树子节点数据：conversation_id -> 优化摘要列表（懒加载）
    listByCid: {},
    loadedCids: [],
    loadingList: {},
    current: null, // 点击子节点后的全量 {rewrite_id, original_description, original_issues, ...三个版本}
  }),
  actions: {
    async loadList(cid, force = false) {
      if (!cid) return
      if (!force && this.loadedCids.includes(cid)) return
      this.loadingList[cid] = true
      try {
        const res = await resumeApi.rewrites(cid)
        this.listByCid[cid] = res.data.rewrites
        if (!this.loadedCids.includes(cid)) this.loadedCids.push(cid)
      } catch (err) {
        // 静默清空会让用户把"加载失败"当成"从来没有优化过"
        this.listByCid[cid] = []
        notifyError(err, '优化历史加载失败')
      } finally {
        this.loadingList[cid] = false
      }
    },
    listOf(cid) {
      return this.listByCid[cid] || []
    },
    async openNode(cid, node) {
      const conv = useConversationStore()
      conv.selectNode('rewrite', cid, node.rewrite_id)
      const res = await resumeApi.rewriteDetail(node.rewrite_id)
      this.current = res.data
    },
    async remove(cid, rewriteId) {
      await resumeApi.removeRewrite(rewriteId)
      const conv = useConversationStore()
      this.listByCid[cid] = (this.listByCid[cid] || []).filter((r) => r.rewrite_id !== rewriteId)
      if (conv.activeNodeId === rewriteId) {
        this.current = null
        await conv.backToConversation()
      }
      conv.loadList().catch(() => {})
    },
  },
})
