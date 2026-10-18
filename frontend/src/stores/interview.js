import { defineStore } from 'pinia'
import { ElMessage } from 'element-plus'
import { interviewApi, notifyError } from '../api'
import { postSse } from '../utils/sse'
import { useConversationStore } from './conversation'
import { useModelStore } from './model'

export const useInterviewStore = defineStore('interview', {
  state: () => ({
    direction: 'general',
    starting: false,
    answering: false,
    // 作答流式的阶段提示：'' | 'evaluating'（在写评价）| 'question'（评价完，在生成下一题）| 'summary'
    // 评价正文本身直接写进 rounds 里那条 pending 占位，不再单独存一份
    liveStage: '',
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
    // 已完成的轮次（不含总结那条与提交中的占位条），用于逐轮展示评分与评价
    answeredRounds: (s) => s.rounds.filter((r) => r.action !== 'summary'),
    // 已落库的轮次：提交瞬间会先插一条 pending 占位，轮次标题不该跟着跳
    doneRounds: (s) => s.rounds.filter((r) => r.action !== 'summary' && !r.pending),
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
            answer: m.content,
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
      const sessionId = this.session.session_id
      this.answering = true
      this.liveStage = 'evaluating'
      // 先插一条占位：提交瞬间回答就变成气泡，输入框清空，评价随后流式填进同一条。
      // 否则从"提交"到"done"这几秒里，用户只能看到输入框里那段被禁用的旧文本，
      // 分不清是提交成功还是在排队。
      this.rounds.push({
        action: 'continue',
        answer: text,
        evaluation: '',
        score: null,
        next_question: '',
        pending: true,
      })
      const at = this.rounds.length - 1
      this.answerText = ''
      let result = null
      try {
        // 流式：评价边生成边显示（首字约 0.5s），题目在流末尾一次性到达。
        // 后端在流结束前完成落库，done 里带的是落库后的最终结果，
        // 所以这里拿到 result 就等于"已经存好了"，不需要再查一次详情。
        await postSse(
          '/interview/answer/stream',
          useModelStore().payloadWithModel({
            session_id: this.session.session_id,
            answer: text,
            question_index: this.session.question_index,
          }),
          (event, data) => {
            if (this.session?.session_id !== sessionId) return
            const row = this.rounds[at]
            if (!row) return
            if (event === 'evaluation') row.evaluation = data.text || ''
            else if (event === 'score') {
              row.score = data.score ?? null
              this.liveStage = data.action === 'summary' ? 'summary' : 'question'
            } else if (event === 'done') result = data
          }
        )
        // 断流时 result 为空：评价可能已经显示出来了，但没落库，必须让用户重试，
        // 不能把预览当成记录留下来。
        if (!result) throw new Error('评价结果不完整，请重试')
        // 作答途中用户可能切到别的场次：后端已经落库了，但当前视图不该被这次结果污染
        if (this.session?.session_id !== sessionId) return
        this.rounds.splice(at, 1, { ...result, answer: text })
        if (result.action === 'continue' && result.next_question) {
          this.session.question_index = result.question_index
        } else if (result.action === 'summary') {
          // 场次结束：同步树上的状态徽标与会话徽标计数
          const cid = useConversationStore().currentId
          const node = (this.sessionsByCid[cid] || []).find((s) => s.session_id === sessionId)
          if (node) {
            node.status = 'completed'
            node.total_score = result.score
            node.round_count = result.total_rounds
          }
          useConversationStore().loadList().catch(() => {})
        }
        if (result.warnings?.length) {
          // 未评分/轮次上限这类降级必须让用户看到，否则"没打分"会被当成"打了 0 分"
          ElMessage({ type: 'warning', message: result.warnings.join('；'), duration: 6000 })
        }
      } catch (err) {
        // 回滚占位并把回答还给输入框：重试一次就行，不用重打一遍
        if (this.session?.session_id === sessionId) {
          this.rounds.splice(at, 1)
          this.answerText = text
        }
        throw new Error(`${err?.message || '评价生成失败'}，请重试一次`)
      } finally {
        this.answering = false
        this.liveStage = ''
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
