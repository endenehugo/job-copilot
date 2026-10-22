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
    // 主区当前激活的节点类型：conversation=聊天视图，interview=面试视图，
    // analysis=JD 分析，rewrite=项目优化，resume=简历生成预览
    // activeNodeId 在 interview 类型下是面试场次 session_id
    activeType: 'conversation',
    activeNodeId: '',
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
      // 选中父节点即回到聊天视图，丢弃面试子节点的选中态
      this.activeType = 'conversation'
      this.activeNodeId = ''
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
    // 选中非会话类型的子节点（interview/analysis/rewrite）：只切选中态，不拉会话
    // detail（各视图有自己的数据源）。selectSeq 前移让还在途的会话 detail 响应作废，
    // 防止晚到的 detail 覆盖回来。
    selectNode(type, cid, nodeId) {
      ++this.selectSeq
      this.loading = false
      if (cid !== this.currentId) {
        this.currentId = cid
        this.detail = null
      }
      this.activeType = type
      this.activeNodeId = nodeId
    },
    // 从子节点视图返回父会话聊天视图
    backToConversation() {
      this.activeType = 'conversation'
      this.activeNodeId = ''
      if (!this.detail && this.currentId) return this.select(this.currentId)
    },
    async refreshDetail() {
      if (this.currentId) await this.select(this.currentId)
    },
    // 只重拉消息与文档，不改变当前视图。
    // 生成简历这类"后台往会话里写了消息"的动作要用它——refreshDetail 会顺带
    // 把 activeType 切回 conversation，正在看简历预览时会把用户弹走。
    async refreshMessages() {
      const cid = this.currentId
      if (!cid) return null
      try {
        const res = await conversationApi.detail(cid)
        if (this.currentId !== cid) return null // 期间已切换到别的会话，丢弃这次结果
        this.detail = res.data
        return res.data
      } catch {
        // 消息刷新失败不影响预览本身，静默即可
        return null
      }
    },
    async remove(cid) {
      await conversationApi.remove(cid)
      await this.loadList()
      // 删除的是当前会话：清空主区（Workspace 的 watch 会联动清空各模块）
      if (cid === this.currentId) {
        this.currentId = ''
        this.detail = null
        this.activeType = 'conversation'
        this.activeNodeId = ''
      }
    },
    // 置顶/取消置顶。排序完全交给服务端，前端不复制一份比较逻辑——
    // 否则两边一旦不一致（比如置顶项的 updated_at 也变了），顺序就会来回跳。
    async togglePin(cid) {
      const target = this.conversations.find((c) => c.conversation_id === cid)
      if (!target) return null
      const next = !target.pinned_at
      await conversationApi.pin(cid, next)
      await this.loadList()
      return next
    },
  },
})
