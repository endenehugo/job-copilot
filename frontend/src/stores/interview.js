import { defineStore } from 'pinia'
import { interviewApi, notifyError } from '../api'
import { useConversationStore } from './conversation'
import { useModelStore } from './model'

export const useInterviewStore = defineStore('interview', {
  state: () => ({
    direction: 'general',
    starting: false,
    answering: false,
    session: null, // {session_id, current_question:{question}, question_index, total_questions}
    rounds: [],    // 每轮 {action, evaluation, score, next_question, total_rounds}
    answerText: '',
    // 会话树子节点数据：conversation_id -> 场次列表（懒加载）
    sessionsByCid: {},
    loadedCids: [],
    loadingSessions: {},
    // 已完成场次的只读回放 {session, messages}
    transcript: null,
  }),
  getters: {
    completed: (s) => s.rounds.length > 0 && s.rounds[s.rounds.length - 1].action === 'summary',
    // 当前题目：优先用上一轮返回的追问，否则用当前的题目消息
    currentQuestion: (s) => {
      if (!s.session) return ''
      const last = s.rounds[s.rounds.length - 1]
      if (last?.next_question) return last.next_question
      return s.session.current_question?.question || ''
    },
    // 结束时后端给出的总结（仅 completed 时有值）
    summary: (s) => {
      const last = s.rounds[s.rounds.length - 1]
      return last && last.action === 'summary' ? last : null
    },
    // 已完成的轮次（不含总结那条），用于逐轮展示评分与评价
    answeredRounds: (s) => s.rounds.filter((r) => r.action !== 'summary'),
  },
  actions: {
    // ---- 会话树子节点（懒加载） ----
    async loadSessions(cid, force = false) {
      if (!cid) return
      if (!force && this.loadedCids.includes(cid)) return
      this.loadingSessions[cid] = true
      try {
        const res = await interviewApi.list(cid)
        this.sessionsByCid[cid] = res.data.sessions
        if (!this.loadedCids.includes(cid)) this.loadedCids.push(cid)
      } catch (err) {
        // 静默清空会让用户把"加载失败"当成"从来没有过面试"
        this.sessionsByCid[cid] = []
        notifyError(err, '面试记录加载失败')
      } finally {
        this.loadingSessions[cid] = false
      }
    },
    sessionsOf(cid) {
      return this.sessionsByCid[cid] || []
    },

    // ---- 打开场次：完成→只读回放；进行中→重建进度可续答 ----
    async openNode(cid, node) {
      const conv = useConversationStore()
      conv.selectNode('interview', cid, node.session_id)
      if (node.status === 'completed') {
        await this.openTranscript(node.session_id)
      } else {
        await this.resumeSession(node.session_id)
      }
    },
    async resumeSession(sessionId) {
      const res = await interviewApi.detail(sessionId)
      const detail = res.data
      this.transcript = null
      this.answerText = ''
      const initialQuestions = detail.session.initial_questions || []
      // 以 interview_messages 为准重建进行中状态：每一对(回答, 评价)构成一轮，
      // 其后的追问消息作为下一题；最后一条题目消息即待回答的当前题。
      const rounds = []
      let currentQuestion = ''
      for (let i = 0; i < detail.messages.length; i++) {
        const m = detail.messages[i]
        if (m.msg_type === 'question') currentQuestion = m.content
        if (m.msg_type === 'answer') {
          const evaluation = detail.messages[i + 1]
          const nextQ = detail.messages[i + 2]
          rounds.push({
            action: 'continue',
            evaluation: evaluation?.evaluation || '',
            score: evaluation?.score ?? null,
            next_question: nextQ?.msg_type === 'question' ? nextQ.content : '',
          })
        }
      }
      this.session = {
        session_id: sessionId,
        current_question: { question: currentQuestion },
        question_index: rounds.length,
        total_questions: initialQuestions.length,
      }
      this.rounds = rounds
    },
    async openTranscript(sessionId) {
      const res = await interviewApi.detail(sessionId)
      this.transcript = res.data
      this.session = null
      this.rounds = []
    },

    // ---- 作答流程 ----
    // jdText：调用方已知的 JD（如「JD 分析」页粘贴但还没点分析的草稿），
    // 为空时后端回退到该会话最近一次分析记录里的 JD 原文。
    async start(jdText = '') {
      const conv = useConversationStore()
      const cid = conv.currentId
      this.starting = true
      try {
        const res = await interviewApi.start(
          useModelStore().payloadWithModel({
            conversation_id: cid,
            direction: this.direction,
            jd_text: (jdText || '').trim(),
          })
        )
        this.session = res.data
        this.rounds = []
        this.answerText = ''
        this.transcript = null
        // 新场次立刻进入主区面试视图，并刷新树与徽标
        conv.selectNode('interview', cid, res.data.session_id)
        await Promise.all([this.loadSessions(cid, true), conv.loadList().catch(() => {})])
      } finally {
        this.starting = false
      }
    },
    async submitAnswer() {
      const text = this.answerText.trim()
      if (!text) throw new Error('请输入回答')
      this.answering = true
      try {
        const res = await interviewApi.answer(
          useModelStore().payloadWithModel({
            session_id: this.session.session_id,
            answer: text,
            question_index: this.session.question_index,
          })
        )
        this.rounds.push(res.data)
        this.answerText = ''
        if (res.data.action === 'continue' && res.data.next_question) {
          this.session.question_index = res.data.question_index
        } else if (res.data.action === 'summary') {
          // 场次结束：同步树上的状态徽标与会话徽标计数
          const cid = useConversationStore().currentId
          const node = (this.sessionsByCid[cid] || []).find((s) => s.session_id === this.session.session_id)
          if (node) {
            node.status = 'completed'
            node.total_score = res.data.score
            node.round_count = res.data.total_rounds
          }
          useConversationStore().loadList().catch(() => {})
        }
      } finally {
        this.answering = false
      }
    },

    // ---- 删除单场 ----
    async removeSession(cid, sessionId) {
      await interviewApi.remove(sessionId)
      const conv = useConversationStore()
      this.sessionsByCid[cid] = (this.sessionsByCid[cid] || []).filter((s) => s.session_id !== sessionId)
      if (conv.activeNodeId === sessionId) {
        this.transcript = null
        this.session = null
        this.rounds = []
        await conv.backToConversation()
      }
      conv.loadList().catch(() => {})
    },
  },
})
