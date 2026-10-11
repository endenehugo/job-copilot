import { defineStore } from 'pinia'
import { interviewApi, notifyError } from '../api'
import { useConversationStore } from './conversation'

export const useInterviewStore = defineStore('interview', {
  state: () => ({
    direction: 'general',
    starting: false,
    answering: false,
    session: null, // {session_id, questions, current_question, question_index, total_questions}
    rounds: [],    // 每轮 {action, evaluation, score, next_question, total_rounds}
    answerText: '',
    history: [],
    transcript: null, // 查看历史回放时 {session, messages}
  }),
  getters: {
    completed: (s) => s.rounds.length > 0 && s.rounds[s.rounds.length - 1].action === 'summary',
    // 当前题目：优先用上一轮返回的追问，否则用开局的第一题
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
    async loadHistory(cid) {
      this.history = []
      this.transcript = null
      if (!cid) return
      try {
        const res = await interviewApi.list(cid)
        this.history = res.data.sessions
      } catch (err) {
        // 静默清空会让用户把"加载失败"当成"从来没有过面试"
        this.history = []
        notifyError(err, '面试历史加载失败')
      }
    },
    async start() {
      const cid = useConversationStore().currentId
      this.starting = true
      try {
        const res = await interviewApi.start({
          conversation_id: cid,
          direction: this.direction,
          jd_text: '',
        })
        this.session = res.data
        this.rounds = []
        this.answerText = ''
        this.transcript = null
        await this.loadHistory(cid)
      } finally {
        this.starting = false
      }
    },
    async submitAnswer() {
      const text = this.answerText.trim()
      if (!text) throw new Error('请输入回答')
      this.answering = true
      try {
        const res = await interviewApi.answer({
          session_id: this.session.session_id,
          answer: text,
          question_index: this.session.question_index,
        })
        this.rounds.push(res.data)
        this.answerText = ''
        if (res.data.action === 'continue' && res.data.next_question) {
          this.session.question_index = res.data.question_index
        }
      } finally {
        this.answering = false
      }
    },
    closeSession() {
      this.session = null
      this.rounds = []
      this.answerText = ''
    },
    async openTranscript(sessionId) {
      const res = await interviewApi.detail(sessionId)
      this.transcript = res.data
      this.session = null
    },
    closeTranscript() {
      this.transcript = null
    },
  },
})
