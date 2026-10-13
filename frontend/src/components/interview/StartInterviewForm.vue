<script setup>
import { ElMessage } from 'element-plus'
import { notifyError } from '../../api'
import { useInterviewStore } from '../../stores/interview'
import { useConversationStore } from '../../stores/conversation'

// 发起面试的表单：主区空态引导与 Composer 工具条对话框共用。
// 前端先校验简历（后端 /interview/start 也会拒绝），避免点了才报错。
const interview = useInterviewStore()
const conv = useConversationStore()

const emit = defineEmits(['started'])

async function onStart() {
  if (!conv.currentId) {
    ElMessage.warning('请先新建或选择一个会话')
    return
  }
  if (!conv.documents.length) {
    ElMessage.warning('当前会话还没有简历：请先上传简历，再发起模拟面试')
    return
  }
  try {
    await interview.start()
    ElMessage.success(`面试已开始，共 ${interview.session?.total_questions ?? '?'} 题`)
    emit('started')
  } catch (err) {
    notifyError(err)
  }
}
</script>

<template>
  <div class="start-form">
    <el-select v-model="interview.direction" size="small" style="width: 100%">
      <el-option label="通用方向" value="general" />
      <el-option label="Python 后端" value="python_backend" />
      <el-option label="Agent / AI 应用" value="agent_ai" />
    </el-select>
    <div class="start-actions">
      <el-button type="primary" size="small" :loading="interview.starting" @click="onStart">
        开始面试
      </el-button>
      <span class="hint">需要会话中已有简历与 JD 分析</span>
    </div>
  </div>
</template>

<style scoped>
.start-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}
</style>
