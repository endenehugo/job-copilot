<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import { renderMarkdown } from '../../utils/markdown'
import SourcePanel from './SourcePanel.vue'
import FileCards from './FileCards.vue'

const props = defineProps({ message: { type: Object, required: true } })

// 流式期间每个 token 都全量跑一遍 markdown 解析 + 消毒 + v-html 替换是 O(n²)，
// 长回答会累积几百毫秒的解析与重排。节流到 ~10fps 肉眼无感，结束时立即渲染
// 保证最终内容完整（不依赖最后一次节流计时器）。
const RENDER_INTERVAL = 100
const html = ref(renderMarkdown(props.message.content))
let timer = null
let lastRender = 0

function renderNow() {
  timer = null
  lastRender = Date.now()
  html.value = renderMarkdown(props.message.content)
}

watch(
  () => props.message.content,
  () => {
    if (!props.message.streaming) {
      renderNow()
      return
    }
    if (timer) return
    timer = setTimeout(renderNow, Math.max(0, RENDER_INTERVAL - (Date.now() - lastRender)))
  }
)
watch(
  () => props.message.streaming,
  (streaming) => {
    if (!streaming) renderNow()
  }
)
onUnmounted(() => {
  if (timer) clearTimeout(timer)
})
</script>

<template>
  <div class="message" :class="message.role">
    <div class="avatar">{{ message.role === 'user' ? '我' : 'AI' }}</div>
    <div class="bubble">
      <div v-html="html"></div><span v-if="message.streaming" class="stream-cursor">▍</span>
      <FileCards v-if="message.role === 'assistant'" :files="message.files" />
      <SourcePanel
        v-if="message.role === 'assistant'"
        :sources="message.sources"
        :verification="message.verification"
      />
    </div>
  </div>
</template>
