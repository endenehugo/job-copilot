<script setup>
import { Close } from '@element-plus/icons-vue'
import { notifyError } from '../../api'
import { useInterviewStore } from '../../stores/interview'

const interview = useInterviewStore()

const MARK = { question: '❓', answer: '💬', evaluation: '📝', summary: '📝' }

async function submit() {
  try {
    await interview.submitAnswer()
  } catch (err) {
    notifyError(err)
  }
}

function onKeydown(e) {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') submit()
}
</script>

<template>
  <div class="panel">
    <div class="panel-header">
      <span>模拟面试（{{ interview.transcript ? '历史回放' : '进行中' }}）</span>
      <div class="actions">
        <el-button v-if="interview.transcript" size="small" text @click="interview.closeTranscript()">返回</el-button>
        <el-button v-else size="small" text @click="interview.closeSession()"><el-icon><Close /></el-icon></el-button>
      </div>
    </div>
    <div class="panel-body">
      <!-- 历史回放 -->
      <template v-if="interview.transcript">
        <div class="interview-summary" style="margin-bottom: 12px">
          方向：{{ interview.transcript.session.direction }} · 状态：{{ interview.transcript.session.status }}
          <template v-if="interview.transcript.session.total_score !== null">
            · 总分 {{ interview.transcript.session.total_score }}
          </template>
          <div v-if="interview.transcript.session.overall_summary" style="margin-top: 6px">
            {{ interview.transcript.session.overall_summary }}
          </div>
        </div>
        <div v-for="m in interview.transcript.messages" :key="m.message_id" class="transcript-item">
          <span class="marker">{{ MARK[m.msg_type] || '💬' }}</span>
          <div style="flex: 1">
            <div>{{ m.content }}</div>
            <div v-if="m.msg_type === 'evaluation' && m.score !== null" class="hint">得分：{{ m.score }}</div>
          </div>
        </div>
      </template>

      <!-- 进行中的会话 -->
      <template v-else-if="interview.session">
        <div v-if="interview.completed" class="interview-summary" style="margin-bottom: 12px">
          🎉 面试结束 · 总分 {{ interview.rounds[interview.rounds.length - 1].score }}
          <div style="margin-top: 6px">{{ interview.rounds[interview.rounds.length - 1].overall_summary }}</div>
        </div>
        <div v-if="!interview.completed" class="interview-question">
          第 {{ interview.session.question_index + 1 }} / {{ interview.session.total_questions }} 题<br />
          {{ interview.session.current_question?.question }}
          <div v-if="interview.session.current_question?.expected_points?.length" class="hint" style="margin-top: 6px">
            参考答题点：{{ interview.session.current_question.expected_points.join('；') }}
          </div>
        </div>
        <div v-for="(r, i) in interview.rounds" :key="i" class="interview-round">
          <div class="round-head"><span>第 {{ i + 1 }} 轮评价</span><span>得分 {{ r.score }}</span></div>
          <div class="evaluation">{{ r.evaluation }}</div>
        </div>
        <el-input
          v-if="!interview.completed"
          v-model="interview.answerText"
          type="textarea"
          :rows="4"
          placeholder="输入你的回答… Ctrl+Enter 提交"
          @keydown="onKeydown"
        />
        <div v-if="!interview.completed" style="margin-top: 8px; text-align: right">
          <el-button type="primary" size="small" :loading="interview.answering" @click="submit">提交回答</el-button>
        </div>
      </template>
    </div>
  </div>
</template>
