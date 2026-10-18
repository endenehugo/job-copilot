<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { exportApi, notifyError } from '../../api'
import { downloadMarkdown } from '../../utils/clipboard'
import { useInterviewStore } from '../../stores/interview'
import { useConversationStore } from '../../stores/conversation'
import { useAnalysisStore } from '../../stores/analysis'

// 主区面试视图：进行中/可续答 + 已完成回放共用一套气泡渲染。
// 进行中场次的状态由 interview.resumeSession 从 interview_messages 重建，
// 刷新或切换会话后点回子节点不会丢进度。
const interview = useInterviewStore()
const conv = useConversationStore()
const analysis = useAnalysisStore()
const exportingId = ref('')

const DIRECTION_LABEL = {
  general: '通用方向',
  python_backend: 'Python 后端',
  agent_ai: 'Agent / AI 应用',
}

function directionText(d) {
  return DIRECTION_LABEL[d] || d || '—'
}

async function onSubmit() {
  try {
    await interview.submitAnswer()
  } catch (err) {
    notifyError(err)
  }
}

async function onRestart() {
  try {
    // 重开一轮沿用同一个 JD 来源：优先本会话粘的草稿，其次后端回退到最近一次分析。
    // 不传时后端只能靠"最近一次分析"，草稿会被静默忽略。
    await interview.start(analysis.jdDraftFor(conv.currentId))
    ElMessage.success('新一轮面试已开始')
  } catch (err) {
    notifyError(err)
  }
}

async function onExport(sessionId) {
  if (!sessionId) return
  exportingId.value = sessionId
  try {
    const res = await exportApi.interview(sessionId)
    downloadMarkdown(`面试记录_${sessionId}`, res.data.markdown)
    ElMessage.success('已导出 Markdown')
  } catch (err) {
    notifyError(err)
  } finally {
    exportingId.value = ''
  }
}

function scoreTagType(score) {
  if (score == null) return 'info'
  if (score >= 80) return 'success'
  if (score >= 60) return 'warning'
  return 'danger'
}

// 回放消息 → 气泡类型
function bubbleType(m) {
  if (m.msg_type === 'answer') return 'user'
  if (m.msg_type === 'evaluation') return 'evaluation'
  return 'question'
}

function msgLabel(m) {
  if (m.msg_type === 'question') return '面试官'
  if (m.msg_type === 'evaluation') return '评价'
  return '我'
}
</script>

<template>
  <div class="iview">
    <div ref="scrollEl" class="iview-scroll">
      <!-- 已完成场次：只读回放 -->
      <template v-if="interview.transcript">
        <header class="iview-head">
          <span>
            面试回放 · {{ directionText(interview.transcript.session?.direction) }}
            <el-tag
              v-if="interview.transcript.session"
              size="small"
              :type="scoreTagType(interview.transcript.session.total_score)"
            >{{ interview.transcript.session.total_score == null ? '未评分' : `总分 ${interview.transcript.session.total_score}` }}</el-tag>
          </span>
          <span class="head-actions">
            <el-button
              v-if="interview.transcript.session"
              size="small"
              :loading="exportingId === interview.transcript.session.session_id"
              @click="onExport(interview.transcript.session.session_id)"
            >导出</el-button>
            <el-button size="small" @click="conv.backToConversation()">返回会话</el-button>
          </span>
        </header>

        <div v-if="interview.transcript.session?.overall_summary" class="summary-card">
          <div class="summary-head">面试总结</div>
          <div>{{ interview.transcript.session.overall_summary }}</div>
        </div>

        <div
          v-for="m in interview.transcript.messages"
          :key="m.message_id"
          class="iv-row"
          :class="`iv-${bubbleType(m)}`"
        >
          <div class="iv-bubble">
            <span class="iv-label">{{ msgLabel(m) }}</span>
            <span v-if="m.msg_type === 'evaluation' && m.score != null" class="iv-score">{{ m.score }} 分</span>
            <div class="iv-text">{{ m.content }}</div>
          </div>
        </div>
        <div v-if="!interview.transcript.messages?.length" class="hint">该场面试没有记录</div>
      </template>

      <!-- 进行中 / 可续答 -->
      <template v-else-if="interview.session">
        <header class="iview-head">
          <span>
            模拟面试 · {{ directionText(interview.direction) }}
            <el-tag size="small" type="warning">进行中</el-tag>
            <span class="round-hint">第 {{ interview.answeredRounds.length + 1 }} 轮</span>
          </span>
          <el-button size="small" @click="conv.backToConversation()">返回会话</el-button>
        </header>

        <!-- 当前题目 -->
        <div class="iv-row iv-question">
          <div class="iv-bubble">
            <span class="iv-label">面试官</span>
            <div class="iv-text" v-loading="interview.answering" element-loading-text="面试官评估中…">
              {{ interview.currentQuestion || '（暂无题目）' }}
            </div>
          </div>
        </div>

        <!-- 逐轮评价 -->
        <div v-for="(r, i) in interview.answeredRounds" :key="`e${i}`" class="iv-row iv-evaluation">
          <div class="iv-bubble">
            <span class="iv-label">第 {{ i + 1 }} 轮评价</span>
            <el-tag size="small" :type="scoreTagType(r.score)">
              {{ r.score == null ? '未评分' : `${r.score} 分` }}
            </el-tag>
            <div class="iv-text">{{ r.evaluation }}</div>
          </div>
        </div>

        <!-- 回答输入 / 总结 -->
        <div v-if="!interview.summary" class="answer-box">
          <el-input
            v-model="interview.answerText"
            type="textarea"
            :rows="4"
            placeholder="输入你的回答… 提交后会给出评分与追问"
          />
          <div class="answer-actions">
            <el-button
              type="primary"
              size="small"
              :loading="interview.answering"
              :disabled="!interview.answerText.trim()"
              @click="onSubmit"
            >提交回答</el-button>
            <span v-if="interview.answering" class="hint">正在评估，约需十几秒…</span>
          </div>
        </div>

        <div v-else class="summary-card">
          <div class="summary-head">
            面试结束 · {{ interview.summary.score == null ? '未评分' : `总分 ${interview.summary.score}` }}
          </div>
          <div>{{ interview.summary.overall_summary || interview.summary.evaluation }}</div>
          <div class="summary-actions">
            <el-button size="small" type="primary" :loading="interview.starting" @click="onRestart">再来一轮</el-button>
            <el-button size="small" @click="conv.backToConversation()">返回会话</el-button>
          </div>
        </div>
      </template>

      <!-- 数据缺失兜底 -->
      <template v-else>
        <div class="hint" style="padding: 24px">面试数据加载中，请稍候…</div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.iview {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  max-width: 860px;
  width: 100%;
  margin: 0 auto;
}
.iview-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 16px 8px;
}
.iview-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 14px;
  font-weight: 600;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--border, #e5e6eb);
  margin-bottom: 12px;
}
.head-actions {
  display: flex;
  gap: 8px;
}
.round-hint {
  font-weight: 400;
  font-size: 12px;
  color: var(--text-secondary, #909399);
  margin-left: 8px;
}
.iv-row {
  display: flex;
  margin-bottom: 10px;
}
.iv-row.iv-user {
  justify-content: flex-end;
}
.iv-row.iv-question,
.iv-row.iv-evaluation {
  justify-content: flex-start;
}
.iv-bubble {
  max-width: 78%;
  background: var(--el-fill-color-light, #f7f8fa);
  border-radius: 8px;
  padding: 8px 12px;
  font-size: 13px;
  line-height: 1.65;
}
.iv-user .iv-bubble {
  background: var(--el-color-primary-light-9, #ecf5ff);
}
.iv-evaluation .iv-bubble {
  background: #fdf6ec;
  border: 1px solid #faecd8;
}
.iv-label {
  display: inline-block;
  font-size: 11px;
  color: var(--el-color-primary);
  font-weight: 600;
  margin-right: 6px;
}
.iv-score {
  font-size: 11px;
  color: var(--el-color-warning);
  font-weight: 600;
  margin-right: 6px;
}
.iv-text {
  white-space: pre-wrap;
  word-break: break-word;
}
.summary-card {
  background: #f0f9eb;
  border: 1px solid #e1f3d8;
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 13px;
  line-height: 1.65;
  margin-bottom: 12px;
}
.summary-head {
  font-weight: 600;
  margin-bottom: 4px;
}
.summary-actions {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}
.answer-box {
  border-top: 1px solid var(--border, #e5e6eb);
  padding-top: 10px;
  margin-top: 4px;
}
.answer-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}
</style>
