import { defineStore } from 'pinia'
import { interviewApi } from '../api'
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
  },
  actions: {
    async loadHistory(cid) {
      this.history = []
      this.transcript = null
      if (!cid) return
      try {
        const res = await interviewApi.list(cid)
        this.history = res.data.sessions
      } catch {
        this.history = []
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
