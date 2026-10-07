<script setup>
import { nextTick, ref, watch } from 'vue'
import { useChatStore } from '../../stores/chat'
import MessageItem from './MessageItem.vue'

const chat = useChatStore()
const el = ref(null)
const showJump = ref(false)

// 用户是否停留在底部附近：决定流式输出时是否自动跟随
const nearBottom = ref(true)

function onScroll() {
  const node = el.value
  if (!node) return
  nearBottom.value = node.scrollHeight - node.scrollTop - node.clientHeight < 80
  showJump.value = !nearBottom.value
}

function scrollToBottom(smooth = false) {
  el.value?.scrollTo({ top: el.value.scrollHeight, behavior: smooth ? 'smooth' : 'auto' })
  nearBottom.value = true
  showJump.value = false
}

watch(
  () => chat.messages,
  async () => {
    await nextTick()
    const last = chat.messages[chat.messages.length - 1]
    if (last?.role === 'user') {
      // 用户刚发出消息：强制跟随
      scrollToBottom(false)
      return
    }
    // 流式输出/新回复：仅在用户本来停在底部时跟随，上翻阅读时不打扰
    if (nearBottom.value) scrollToBottom(false)
  },
  { deep: true }
)
</script>

<template>
  <div class="chat-wrap">
    <div ref="el" class="chatlogs" @scroll="onScroll">
      <MessageItem v-for="(m, i) in chat.messages" :key="i" :message="m" />
      <div v-if="chat.sending && !chat.messages[chat.messages.length - 1]?.streaming" class="message assistant">
        <div class="avatar">AI</div>
        <div class="bubble hint">正在思考，请稍候…</div>
      </div>
    </div>
    <div v-if="showJump" class="scroll-bottom-btn" title="回到底部" @click="scrollToBottom(true)">↓</div>
  </div>
</template>
