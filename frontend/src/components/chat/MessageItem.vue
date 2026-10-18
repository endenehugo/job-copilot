<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import { renderMarkdown } from '../../utils/markdown'
import { copyText } from '../../utils/clipboard'
import SourcePanel from './SourcePanel.vue'
import FileCards from './FileCards.vue'
import AppIcon from '../shell/AppIcon.vue'

const props = defineProps({ message: { type: Object, required: true } })

// 用户消息按纯文本渲染：粘贴的 JD 里常带 "##"、"-"、表格，
// 走 markdown 会在气泡里渲染出大标题，与截图里的"一段话"完全不是一回事。
// 图片是发送时拼进 content 的 markdown，单独拆出来当附件显示。
const userImages = computed(() =>
  [...(props.message.content || '').matchAll(/!\[[^\]]*\]\(([^)]+)\)/g)].map((m) => m[1])
)
const userText = computed(() => (props.message.content || '').replace(/!\[[^\]]*\]\([^)]+\)/g, '').trim())

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

const copied = ref(false)
let copyTimer = null
async function onCopy() {
  if (!props.message.content) return
  const ok = await copyText(props.message.content)
  if (!ok) return
  copied.value = true
  clearTimeout(copyTimer)
  copyTimer = setTimeout(() => (copied.value = false), 1600)
}
onUnmounted(() => clearTimeout(copyTimer))
</script>

<template>
  <div class="msg" :class="`msg--${message.role}`">
    <!-- 用户：右对齐浅灰气泡，纯文本 + 图片附件 -->
    <div v-if="message.role === 'user'" class="msg-user-bubble">
      <img v-for="(src, i) in userImages" :key="i" :src="src" alt="附件图片" />
      <div v-if="userText" class="msg-user-text">{{ userText }}</div>
    </div>

    <!-- 助手：无头像、无气泡、整列纯文本 -->
    <div v-else class="msg-assistant">
      <div v-if="message.toolStatus && message.streaming" class="msg-tool">
        <span class="msg-tool-dot"></span>
        正在调用 {{ toolName(message.toolStatus.name) }} · 第 {{ message.toolStatus.step }} 步
      </div>
      <div v-else-if="message.toolStatus" class="msg-tool is-done">
        已执行 {{ message.toolStatus.step }} 步工具调用
      </div>

      <div class="msg-md" v-html="html"></div>
      <span v-if="message.streaming" class="msg-caret">▍</span>

      <div v-if="message.warnings && message.warnings.length" class="msg-warnings">
        <div v-for="(w, i) in message.warnings" :key="i" class="msg-warning">
          <AppIcon name="warning" :size="14" />
          <span>{{ w }}</span>
        </div>
      </div>

      <FileCards :files="message.files" />
      <SourcePanel :sources="message.sources" :verification="message.verification" />

      <div v-if="!message.streaming && message.content" class="msg-actions">
        <button class="msg-action" title="复制回答" @click="onCopy">
          <AppIcon :name="copied ? 'check' : 'copy'" :size="15" />
          <span>{{ copied ? '已复制' : '复制' }}</span>
        </button>
      </div>
    </div>
  </div>
</template>
