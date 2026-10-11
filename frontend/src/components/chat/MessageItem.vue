<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import { renderMarkdown } from '../../utils/markdown'
import SourcePanel from './SourcePanel.vue'
import FileCards from './FileCards.vue'

const props = defineProps({ message: { type: Object, required: true } })

const TOOL_NAMES = {
  web_search: '联网搜索',
  web_search_tool: '联网搜索',
  word_document: '生成 Word 文档',
  html_document: '生成网页文档',
  jd_parser: 'JD 解析',
  resume_score: '简历评分',
  project_rewrite: '项目改写',
  mock_interview: '模拟面试',
  multiply: '数值计算',
  skill: '技能查询',
}

function toolName(name) {
  return TOOL_NAMES[String(name || '').toLowerCase()] || name || '工具'
}

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
      <div v-if="message.toolStatus && message.streaming" class="tool-status">
        <span class="tool-dot"></span>
        正在调用 {{ toolName(message.toolStatus.name) }} · 第 {{ message.toolStatus.step }} 步
      </div>
      <div v-else-if="message.toolStatus && !message.streaming" class="tool-status done">
        已执行 {{ message.toolStatus.step }} 步工具调用
      </div>
      <div v-html="html"></div><span v-if="message.streaming" class="stream-cursor">▍</span>
      <div v-if="message.warnings && message.warnings.length" class="msg-warnings">
        <div v-for="(w, i) in message.warnings" :key="i">⚠ {{ w }}</div>
      </div>
      <FileCards v-if="message.role === 'assistant'" :files="message.files" />
      <SourcePanel
        v-if="message.role === 'assistant'"
        :sources="message.sources"
        :verification="message.verification"
      />
    </div>
  </div>
</template>
