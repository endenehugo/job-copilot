<script setup>
import { watch } from 'vue'
import { startDialog } from '../../utils/startDialog'
import { useAnalysisStore } from '../../stores/analysis'
import { useConversationStore } from '../../stores/conversation'
import AppIcon from '../shell/AppIcon.vue'
import StartInterviewForm from './StartInterviewForm.vue'
import StartAnalysisForm from '../analysis/StartAnalysisForm.vue'
import StartRewriteForm from '../rewrite/StartRewriteForm.vue'

// 统一发起入口：面试 / JD 分析 / 项目优化。
// 状态存于 startDialog 单例（utils/startDialog.js），空态卡片与 Composer 共用。
// 标签页不用 el-tabs：默认的整宽下划线 + 小字号在弹窗里显得像后台表单，
// 改成同色系分段控件（浅灰底 + 白色激活块），边框更轻、点击区域更大。
const TABS = [
  { name: 'interview', label: '模拟面试', icon: 'target' },
  { name: 'analysis', label: 'JD 分析', icon: 'chart' },
  { name: 'rewrite', label: '项目优化', icon: 'pen' },
]

const analysis = useAnalysisStore()
const conv = useConversationStore()

// 打开弹窗时把 JD 输入框切到当前会话的草稿。
// 不做这一步时，输入框里可能还停着上一个会话的 JD——用户会以为它属于当前会话。
watch(
  () => startDialog.visible,
  (open) => {
    if (open) analysis.loadJdDraft(conv.currentId)
  }
)

function onStarted() {
  startDialog.visible = false
}
</script>

<template>
  <el-dialog
    v-model="startDialog.visible"
    title="发起新任务"
    width="min(600px, 92vw)"
    class="task-dialog"
    :append-to-body="true"
  >
    <div class="task-tabs" role="tablist">
      <button
        v-for="tab in TABS"
        :key="tab.name"
        class="task-tab"
        :class="{ 'is-active': startDialog.tab === tab.name }"
        role="tab"
        :aria-selected="startDialog.tab === tab.name"
        @click="startDialog.tab = tab.name"
      >
        <AppIcon :name="tab.icon" :size="16" />
        <span>{{ tab.label }}</span>
      </button>
    </div>

    <!-- v-show 而非 v-if：切标签页不能丢掉已经输入的 JD / 项目描述 -->
    <StartInterviewForm v-show="startDialog.tab === 'interview'" @started="onStarted" />
    <StartAnalysisForm v-show="startDialog.tab === 'analysis'" @started="onStarted" />
    <StartRewriteForm v-show="startDialog.tab === 'rewrite'" @started="onStarted" />
  </el-dialog>
</template>
