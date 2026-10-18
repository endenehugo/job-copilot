<script setup>
import { ref } from 'vue'
import { useConversationStore } from '../../stores/conversation'
import { useResumeGenStore } from '../../stores/resumeGen'
import AppIcon from '../shell/AppIcon.vue'

// 主会话里的简历卡片。载荷来自 message.meta（后端 conversation_message_meta 表）。
// 只信 generation_id：文件名会变（HTML 丢失时服务端重渲染会换名），
// 所以下载链接必须在点的时候从详情接口取，不能写死在消息里。
const props = defineProps({ meta: { type: Object, required: true } })

const conv = useConversationStore()
const gen = useResumeGenStore()
const busy = ref(false)

function onOpen() {
  if (!props.meta?.generation_id) return
  conv.selectNode('resume', conv.currentId, props.meta.generation_id)
}

async function onDownload() {
  if (!props.meta?.generation_id || busy.value) return
  busy.value = true
  try {
    const detail = await gen.open(props.meta.generation_id)
    const url = detail?.html_url
    if (!url) return
    const link = document.createElement('a')
    link.href = url
    link.download = detail.filename || 'resume.html'
    document.body.appendChild(link)
    link.click()
    link.remove()
  } catch {
    /* 错误提示已由 store 统一处理 */
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="resume-card">
    <div class="rc-icon">
      <AppIcon name="file" :size="18" />
    </div>
    <div class="rc-body">
      <div class="rc-title">{{ meta.title || '简历' }}</div>
      <div class="rc-meta">
        <span>{{ meta.meta_line }}</span>
        <span v-if="meta.warnings_count" class="rc-flag">
          {{ meta.warnings_count }} 处需核对
        </span>
      </div>
    </div>
    <div class="rc-actions">
      <button class="rc-btn" :disabled="busy" @click="onDownload">
        {{ busy ? '准备中' : '下载' }}
      </button>
      <button class="rc-btn rc-btn--primary" @click="onOpen">打开预览</button>
    </div>
  </div>
</template>

<style scoped>
.resume-card {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 12px;
  padding: 12px 14px;
  border: 1px solid var(--line);
  border-radius: var(--r-card);
  background: var(--surface-1);
}
.rc-icon {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 9px;
  background: var(--surface-active);
  color: var(--text-2);
}
.rc-body {
  flex: 1;
  min-width: 0;
}
.rc-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rc-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 2px;
  font-size: 12px;
  color: var(--text-3);
}
.rc-flag {
  color: #b45309;
  background: #fef3c7;
  border-radius: 4px;
  padding: 0 5px;
}
.rc-actions {
  flex: none;
  display: flex;
  gap: 8px;
}
.rc-btn {
  padding: 5px 12px;
  font-size: 12px;
  border-radius: 7px;
  border: 1px solid var(--line-strong);
  background: transparent;
  color: var(--text-1);
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease;
}
.rc-btn:hover {
  background: var(--surface-sunken);
}
.rc-btn:disabled {
  opacity: 0.6;
  cursor: default;
}
.rc-btn--primary {
  background: var(--text-1);
  border-color: var(--text-1);
  color: var(--surface-1);
}
.rc-btn--primary:hover {
  opacity: 0.88;
  background: var(--text-1);
}
</style>
