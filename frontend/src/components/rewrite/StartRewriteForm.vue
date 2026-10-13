<script setup>
import { ElMessage } from 'element-plus'
import { notifyError } from '../../api'
import { useAnalysisStore } from '../../stores/analysis'
import { useConversationStore } from '../../stores/conversation'

// 发起项目优化的表单（发起对话框的"优化"标签页）。
// 优化完成后主区自动切到结果视图（后端已落库，成为会话子节点）。
const analysis = useAnalysisStore()
const conv = useConversationStore()

const emit = defineEmits(['started'])

async function onRewrite() {
  if (!conv.currentId) {
    ElMessage.warning('请先新建或选择一个会话')
    return
  }
  try {
    await analysis.rewrite()
    ElMessage.success('优化完成，结果已保存')
    emit('started')
  } catch (err) {
    notifyError(err)
  }
}
</script>

<template>
  <div class="start-form">
    <el-input
      v-model="analysis.rewriteText"
      type="textarea"
      :rows="5"
      placeholder="粘贴需要优化的项目经历描述… 将结合会话内简历给出通用版、后端版与 Agent 版"
    />
    <div class="start-actions">
      <el-button type="primary" size="small" :loading="analysis.rewriting" @click="onRewrite">开始优化</el-button>
      <span class="hint">无需简历也可优化，有简历则结合简历上下文</span>
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
