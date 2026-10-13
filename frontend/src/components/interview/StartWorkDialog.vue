<script setup>
import { startDialog } from '../../utils/startDialog'
import StartInterviewForm from './StartInterviewForm.vue'
import StartAnalysisForm from '../analysis/StartAnalysisForm.vue'
import StartRewriteForm from '../rewrite/StartRewriteForm.vue'

// 统一发起入口：面试 / JD 分析 / 项目优化三标签页。
// 状态存于 startDialog 单例（utils/startDialog.js），空态卡片与 Composer 共用。
function onStarted() {
  startDialog.visible = false
}
</script>

<template>
  <el-dialog
    v-model="startDialog.visible"
    title="发起新任务"
    width="480px"
    :append-to-body="true"
  >
    <el-tabs v-model="startDialog.tab">
      <el-tab-pane label="🎤 模拟面试" name="interview">
        <StartInterviewForm @started="onStarted" />
      </el-tab-pane>
      <el-tab-pane label="📊 JD 分析" name="analysis">
        <StartAnalysisForm @started="onStarted" />
      </el-tab-pane>
      <el-tab-pane label="✏️ 项目优化" name="rewrite">
        <StartRewriteForm @started="onStarted" />
      </el-tab-pane>
    </el-tabs>
  </el-dialog>
</template>
