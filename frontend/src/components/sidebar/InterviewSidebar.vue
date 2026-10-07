<script setup>
import { notifyError } from '../../api'
import { useInterviewStore } from '../../stores/interview'
import { useUiStore } from '../../stores/ui'

const interview = useInterviewStore()
const ui = useUiStore()

async function onStart() {
  try {
    await interview.start()
    ui.openDrawer()
  } catch (err) {
    notifyError(err)
  }
}

function fmt(ts) {
  return (ts || '').replace('T', ' ').slice(5, 16)
}
</script>

<template>
  <div>
    <el-select v-model="interview.direction" size="small" style="width: 100%">
      <el-option label="通用方向" value="general" />
      <el-option label="Python 后端" value="python_backend" />
      <el-option label="Agent / AI 应用" value="agent_ai" />
    </el-select>
    <div style="margin: 8px 0 10px">
      <el-button type="primary" size="small" :loading="interview.starting" @click="onStart">开始面试</el-button>
      <span class="hint" style="margin-left: 8px">需要会话中已有简历与 JD 分析</span>
    </div>
    <div v-if="interview.history.length" class="hint" style="margin-bottom: 4px">历史面试</div>
    <div
      v-for="s in interview.history.slice(0, 3)"
      :key="s.session_id"
      class="history-item"
      @click="interview.openTranscript(s.session_id).then(() => ui.openDrawer())"
    >
      <span>{{ s.direction }} · {{ fmt(s.updated_at) }}</span>
      <el-tag size="small" :type="s.status === 'completed' ? 'success' : 'info'">
        {{ s.status === 'completed' ? `${s.total_score ?? '—'}分` : '进行中' }}
      </el-tag>
    </div>
  </div>
</template>
