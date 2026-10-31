<script setup>
import { nextTick, ref, watch } from 'vue'
import { useChatStore } from '../../stores/chat'
import { notifyError } from '../../api'
import MessageItem from './MessageItem.vue'

const chat = useChatStore()
const el = ref(null)
const showJump = ref(false)

// 首屏试玩引导：访客（往往是第一次点开链接的面试官）不会自己想 prompt，
// 点一下就要能看到 Agent 干活。三条分别演示：文件交付 / 多轮工具循环 / 联网搜索。
const starters = [
  {
    icon: '📄',
    text: '根据本会话里的材料，帮我生成一份可下载的 HTML 简历；缺什么信息就直接问我',
  },
  {
    icon: '🎤',
    text: '和我模拟一场 Python 后端工程师的一对一面试，一次只问一个问题，我回答后你再点评并追问',
  },
  {
    icon: '🔍',
    text: '联网搜一下 2026 年校招 AI 应用开发岗的高频要求，整理成一份要点清单',
  },
]

function start(text) {
  if (chat.sending) return
  chat.sendStream(text).catch((err) => notifyError(err, '发送失败'))
}

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
      <div v-if="!chat.messages.length" class="empty-state">
        <p class="empty-title">从一个例子开始</p>
        <div class="starter-list">
          <button
            v-for="item in starters"
            :key="item.text"
            class="starter"
            :disabled="chat.sending"
            @click="start(item.text)"
          >
            <span class="starter-icon">{{ item.icon }}</span>
            <span class="starter-text">{{ item.text }}</span>
          </button>
        </div>
      </div>
      <MessageItem v-for="(m, i) in chat.messages" :key="i" :message="m" />

      <div v-if="chat.sending && !chat.messages[chat.messages.length - 1]?.streaming" class="msg msg--assistant">
        <div class="msg-assistant">
          <div class="thinking"><span></span><span></span><span></span></div>
        </div>
      </div>
    </div>

    <button v-if="showJump" class="scroll-bottom-btn" title="回到底部" @click="scrollToBottom(true)">
      ↓
    </button>
  </div>
</template>

<style scoped>
/* 空会话首屏：居中引导卡。样式走全局 token，与主界面同一套中性色 */
.empty-state {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 18px;
  padding: 24px;
}
.empty-title {
  margin: 0;
  font-size: 22px;
  font-weight: 600;
  color: var(--text-1);
}
.starter-list {
  width: min(560px, 100%);
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.starter {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  text-align: left;
  padding: 13px 16px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--surface-1);
  color: var(--text-1);
  font-size: 14px;
  line-height: 1.5;
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease;
}
.starter:hover:not(:disabled) {
  background: var(--surface-hover);
  border-color: var(--line-strong);
}
.starter:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.starter-icon {
  flex: none;
  font-size: 18px;
}
.starter-text {
  min-width: 0;
}
</style>
