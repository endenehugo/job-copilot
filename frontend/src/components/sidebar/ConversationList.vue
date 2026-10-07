<script setup>
import { useConversationStore } from '../../stores/conversation'
import { notifyError } from '../../api/client'

const store = useConversationStore()

async function onCreate() {
  try {
    await store.create()
  } catch (err) {
    notifyError(err)
  }
}

async function onSelect(id) {
  if (id === store.currentId) return
  try {
    await store.select(id)
  } catch (err) {
    notifyError(err)
  }
}

function fmt(ts) {
  return (ts || '').replace('T', ' ').slice(0, 16)
}
</script>

<template>
  <div>
    <div style="display: flex; justify-content: flex-end; margin-bottom: 8px">
      <el-button type="primary" size="small" @click="onCreate">新建对话</el-button>
    </div>
    <div v-if="!store.conversations.length" class="hint">暂无会话，点击右上角新建。</div>
    <div
      v-for="c in store.conversations"
      :key="c.conversation_id"
      class="conversation-item"
      :class="{ active: c.conversation_id === store.currentId }"
      @click="onSelect(c.conversation_id)"
    >
      <div class="title">{{ c.title }}</div>
      <div class="preview">{{ c.last_message_preview || fmt(c.updated_at) }}</div>
    </div>
  </div>
</template>
