<script setup>
import { ref, onMounted, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { exportApi, notifyError } from '../../api'
import { downloadMarkdown } from '../../utils/clipboard'
import { useInterviewStore } from '../../stores/interview'
import { useConversationStore } from '../../stores/conversation'

const interview = useInterviewStore()
const conv = useConversationStore()
const exportingId = ref('')

const DIRECTION_LABEL = {
  general: '通用方向',
  python_backend: 'Python 后端',
  agent_ai: 'Agent / AI 应用',
}

async function onStart() {
  try {
    await interview.start()
    ElMessage.success(`面试已开始（共 ${interview.session?.total_questions ?? '?'} 题）`)
  } catch (err) {
    notifyError(err)
  }
}

async function onSubmit() {
  try {
    await interview.submitAnswer()
  } catch (err) {
    notifyError(err)
  }
}

function onEnd() {
  interview.closeSession()
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

async function onOpenTranscript(sessionId) {
  try {
    await interview.openTranscript(sessionId)
  } catch (err) {
    notifyError(err)
  }
}

function onCloseTranscript() {
  interview.closeTranscript()
}

function fmt(ts) {
  return (ts || '').replace('T', ' ').slice(5, 16)
}

function scoreTagType(score) {
  if (score == null) return 'info'
  if (score >= 80) return 'success'
  if (score >= 60) return 'warning'
  return 'danger'
}

function msgLabel(m) {
  if (m.msg_type === 'question') return '面试官'
  if (m.msg_type === 'evaluation') return '评价'
  return '我'
}

// 切换会话后旧的面试状态属于上一个会话，必须清掉，否则会显示别的会话的题目
watch(
  () => conv.currentId,
  (id) => {
    interview.closeSession()
    interview.loadHistory(id)
  }
)

onMounted(() => interview.loadHistory(conv.currentId))
</script>

<template>
  <div>
    <!-- 历史回放 -->
    <template v-if="interview.transcript">
      <div class="iv-head">
        <span>
          面试回放 ·
          {{ DIRECTION_LABEL[interview.transcript.session?.direction] || interview.transcript.session?.direction || '—' }}
        </span>
        <span style="display: flex; gap: 6px">
          <el-button
            v-if="interview.transcript.session"
            size="small"
            :loading="exportingId === interview.transcript.session.session_id"
            @click="onExport(interview.transcript.session.session_id)"
          >导出</el-button>
          <el-button size="small" @click="onCloseTranscript">返回</el-button>
        </span>
      </div>
      <div v-if="interview.transcript.session?.overall_summary" class="iv-summary">
        <div class="iv-summary-head">
          总分 {{ interview.transcript.session.total_score ?? '未评分' }}
        </div>
        <div>{{ interview.transcript.session.overall_summary }}</div>
      </div>
      <div
        v-for="m in interview.transcript.messages"
        :key="m.message_id"
        class="iv-msg"
        :class="`iv-msg-${m.msg_type || m.role}`"
      >
        <span class="iv-label">{{ msgLabel(m) }}</span>
        <span class="iv-msg-text">{{ m.content }}</span>
      </div>
      <div v-if="!interview.transcript.messages?.length" class="hint">该场面试没有记录</div>
    </template>

    <!-- 进行中 / 已结束 -->
    <template v-else-if="interview.session">
      <div class="iv-head">
        <span>
          第 {{ interview.answeredRounds.length + 1 }} 轮 ·
          {{ DIRECTION_LABEL[interview.direction] || interview.direction }}
        </span>
        <el-button size="small" @click="onEnd">结束</el-button>
      </div>

      <!-- B29：评估可达十几秒，主区 loading 而不是只有小字提示 -->
      <div v-loading="interview.answering" element-loading-text="面试官评估中…">
        <div class="iv-question">
          <span class="iv-label">面试官</span>
          <span>{{ interview.currentQuestion || '（暂无题目）' }}</span>
        </div>
      </div>

      <template v-if="!interview.summary">
        <el-input
          v-model="interview.answerText"
          type="textarea"
          :rows="4"
          placeholder="输入你的回答… 提交后会给出评分与追问"
        />
        <div style="display: flex; gap: 8px; margin-top: 8px">
          <el-button
            type="primary"
            size="small"
            :loading="interview.answering"
            :disabled="!interview.answerText.trim()"
            @click="onSubmit"
          >
            提交回答
          </el-button>
          <span v-if="interview.answering" class="hint">正在评估，约需十几秒…</span>
        </div>
      </template>

      <div v-else class="iv-summary">
        <div class="iv-summary-head">
          面试结束 · {{ interview.summary.score == null ? '未评分' : `总分 ${interview.summary.score}` }}
        </div>
        <div>{{ interview.summary.overall_summary || interview.summary.evaluation }}</div>
        <el-button size="small" style="margin-top: 8px" :loading="interview.starting" @click="onStart">
          再来一轮
        </el-button>
      </div>

      <div v-if="interview.answeredRounds.length" class="hint" style="margin-top: 10px">
        逐轮评价
      </div>
      <div v-for="(r, i) in interview.answeredRounds" :key="i" class="iv-round">
        <div class="iv-round-head">
          <span>第 {{ i + 1 }} 轮</span>
          <!-- score 为 null 表示模型没返回评分，不能显示成 0 分 -->
          <el-tag size="small" :type="scoreTagType(r.score)">
            {{ r.score == null ? '未评分' : `${r.score} 分` }}
          </el-tag>
        </div>
        <div class="iv-round-text">{{ r.evaluation }}</div>
      </div>
    </template>

    <!-- 未开始 -->
    <template v-else>
      <el-select v-model="interview.direction" size="small" style="width: 100%">
        <el-option label="通用方向" value="general" />
        <el-option label="Python 后端" value="python_backend" />
        <el-option label="Agent / AI 应用" value="agent_ai" />
      </el-select>
      <div style="margin: 8px 0 10px">
        <el-button type="primary" size="small" :loading="interview.starting" @click="onStart">
          开始面试
        </el-button>
        <span class="hint" style="margin-left: 8px">需要会话中已有简历与 JD 分析</span>
      </div>
    </template>

    <!-- 历史 -->
    <template v-if="!interview.transcript">
      <div v-if="interview.history.length" class="hint" style="margin-bottom: 4px">
        历史面试（点击查看回放）
      </div>
      <div
        v-for="s in interview.history.slice(0, 5)"
        :key="s.session_id"
        class="history-item"
        @click="onOpenTranscript(s.session_id)"
      >
        <span>
          {{ DIRECTION_LABEL[s.direction] || s.direction }} · {{ fmt(s.updated_at) }}
        </span>
        <span style="display: flex; gap: 4px; align-items: center">
          <el-button size="small" text :loading="exportingId === s.session_id" @click.stop="onExport(s.session_id)">
            导出
          </el-button>
          <el-tag size="small" :type="s.status === 'completed' ? 'success' : 'info'">
            {{ s.status === 'completed' ? `${s.total_score ?? '未评分'}分` : '进行中' }}
          </el-tag>
        </span>
      </div>
    </template>
  </div>
</template>

<style scoped>
.iv-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 8px;
}
.iv-question {
  background: var(--el-fill-color-light, #f7f8fa);
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 13px;
  line-height: 1.6;
  margin-bottom: 8px;
}
.iv-label {
  display: inline-block;
  font-size: 11px;
  color: var(--el-color-primary);
  font-weight: 600;
  margin-right: 6px;
}
.iv-summary {
  background: #f0f9eb;
  border: 1px solid #e1f3d8;
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 13px;
  line-height: 1.6;
}
.iv-summary-head {
  font-weight: 600;
  margin-bottom: 4px;
}
.iv-round {
  border-top: 1px dashed var(--border, #e5e6eb);
  padding: 8px 0;
  font-size: 13px;
}
.iv-round-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}
.iv-round-text {
  color: var(--text-secondary, #606266);
  line-height: 1.6;
  white-space: pre-wrap;
}
.iv-msg {
  padding: 6px 0;
  font-size: 13px;
  line-height: 1.6;
  border-bottom: 1px dashed var(--border, #e5e6eb);
  white-space: pre-wrap;
}
.iv-msg-text {
  color: var(--text-secondary, #606266);
}
.history-item {
  cursor: pointer;
}
</style>
