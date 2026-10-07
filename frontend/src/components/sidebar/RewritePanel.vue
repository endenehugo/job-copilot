<script setup>
import { ElMessage } from 'element-plus'
import { notifyError } from '../../api'
import { useAnalysisStore } from '../../stores/analysis'
import { useUiStore } from '../../stores/ui'

const analysis = useAnalysisStore()
const ui = useUiStore()

async function onRewrite() {
  try {
    await analysis.rewrite()
    ui.openDrawer()
    ElMessage.success('优化完成')
  } catch (err) {
    notifyError(err)
  }
}
</script>

<template>
  <div>
    <el-input
      v-model="analysis.rewriteText"
      type="textarea"
      :rows="4"
      placeholder="粘贴需要优化的项目经历描述… 将结合会话内简历给出通用版、后端版与 Agent 版"
    />
    <div style="margin-top: 8px">
      <el-button type="primary" size="small" :loading="analysis.rewriting" @click="onRewrite">开始优化</el-button>
    </div>
  </div>
</template>
