<script setup>
import { useConversationStore } from '../../stores/conversation'
import { notifyError } from '../../api'
import { openStartDialog } from '../../utils/startDialog'
import AppIcon from './AppIcon.vue'

const emit = defineEmits(['open-panel'])
const conv = useConversationStore()

// 只列真实存在的六个动作。截图里的"图像 / 定时任务 / 插件 / Codex"后端都没有，
// 照抄会得到一排点不动的空壳。
const NAV = [
  { key: 'interview', icon: 'target', label: '模拟面试', run: () => openStartDialog('interview') },
  { key: 'analysis', icon: 'chart', label: 'JD 分析', run: () => openStartDialog('analysis') },
  { key: 'rewrite', icon: 'pen', label: '项目优化', run: () => openStartDialog('rewrite') },
  { key: 'resume', icon: 'file', label: '生成简历', run: () => openStartDialog('resume') },
  { key: 'docs', icon: 'folder', label: '资料库', run: () => emit('open-panel', 'docs') },
  { key: 'kb', icon: 'library', label: '知识库', run: () => emit('open-panel', 'kb') },
]

async function onNewChat() {
  try {
    await conv.create()
  } catch (err) {
    notifyError(err)
  }
}
</script>

<template>
  <nav class="sidebar-nav">
    <button class="nav-row nav-row--strong" @click="onNewChat">
      <AppIcon name="newChat" :size="18" />
      <span>新对话</span>
    </button>

    <div class="nav-group">
      <button v-for="item in NAV" :key="item.key" class="nav-row" @click="item.run()">
        <AppIcon :name="item.icon" :size="18" />
        <span>{{ item.label }}</span>
      </button>
    </div>
  </nav>
</template>
