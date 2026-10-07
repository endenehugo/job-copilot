<script setup>
import { nextTick, ref, watch } from 'vue'
import { useChatStore } from '../../stores/chat'
import MessageItem from './MessageItem.vue'

const chat = useChatStore()
const el = ref(null)

watch(
  () => chat.messages.length,
  async () => {
    await nextTick()
    el.value?.scrollTo({ top: el.value.scrollHeight, behavior: 'smooth' })
  }
)
</script>

<template>
  <div ref="el" class="chatlogs">
    <MessageItem v-for="(m, i) in chat.messages" :key="i" :message="m" />
    <div v-if="chat.sending" class="message assistant">
      <div class="avatar">AI</div>
      <div class="bubble hint">正在思考，请稍候…</div>
    </div>
  </div>
</template>
