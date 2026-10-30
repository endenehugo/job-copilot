<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import { renderMarkdown } from '../../utils/markdown'
import { copyText } from '../../utils/clipboard'
import SourcePanel from './SourcePanel.vue'
import FileCards from './FileCards.vue'
import ResumeCard from './ResumeCard.vue'
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
  resume_generate_tool: '生成简历',
  resume_generate: '生成简历',
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

// 工具轮静默计时：非流式 invoke 期间后端没有 delta，
// 只靠一个转圈图标用户无法判断是慢还是死了，这里把已等待秒数显示出来。
const elapsed = ref(0)
let ticker = null
function startTicker() {
  clearInterval(ticker)
  const start = props.message.startedAt || Date.now()
  elapsed.value = Math.floor((Date.now() - start) / 1000)
  ticker = setInterval(() => {
    elapsed.value = Math.floor((Date.now() - start) / 1000)
  }, 1000)
}
function stopTicker() {
  clearInterval(ticker)
  ticker = null
}
watch(
  () => props.message.streaming,
  (streaming) => (streaming ? startTicker() : stopTicker()),
  { immediate: true }
)
onUnmounted(stopTicker)

const toolLabel = computed(() => {
  const st = props.message.toolStatus
  if (!st) return ''
  // thinking：正在让模型生成工具参数（静默期）；tool_status：工具已经在执行
  if (st.thinking) return `正在生成（第 ${st.round}/${st.max_rounds} 轮）`
  return `正在调用 ${toolName(st.name)} · 第 ${st.step} 步`
})

// 思考过程：模型开思考时首字要等几十秒，这期间只有 reasoning_content 在增长。
// 折叠区默认展开（等待时用户需要看到东西），流结束后自动收起。
// 只渲染末尾一段——长文档任务能产生一两万字的思考，全量塞进 DOM 会拖慢重排。
const THINKING_MAX_CHARS = 4000
const thinkingShown = ref('')
let thinkingTimer = null

function tailOf(text) {
  const value = text || ''
  if (value.length <= THINKING_MAX_CHARS) return value
  return `…（已省略前 ${value.length - THINKING_MAX_CHARS} 字）\n${value.slice(-THINKING_MAX_CHARS)}`
}

watch(
  () => props.message.reasoning,
  () => {
    // 逐 token 更新，150ms 节流；流结束时立刻补最后一次，保证尾部完整
    if (!props.message.streaming) {
      if (thinkingTimer) {
        clearTimeout(thinkingTimer)
        thinkingTimer = null
      }
      thinkingShown.value = tailOf(props.message.reasoning)
      return
    }
    if (thinkingTimer) return
    thinkingTimer = setTimeout(() => {
      thinkingTimer = null
      thinkingShown.value = tailOf(props.message.reasoning)
    }, 150)
  },
  { immediate: true }
)
onUnmounted(() => clearTimeout(thinkingTimer))

function formatElapsed(seconds) {
  if (seconds < 60) return `${seconds}s`
  return `${Math.floor(seconds / 60)}m${String(seconds % 60).padStart(2, '0')}s`
}

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
      <!-- 思考过程：只在本次流式期间存在，刷新即消失（不落库） -->
      <details v-if="message.reasoning" class="msg-thinking" :open="!!message.streaming">
        <summary class="msg-thinking-head">
          {{ message.streaming ? '思考中…' : '思考过程' }}
          <span class="msg-thinking-count">{{ message.reasoning.length }} 字</span>
        </summary>
        <div class="msg-thinking-body">{{ thinkingShown }}</div>
      </details>

      <div v-if="message.streaming && message.toolStatus" class="msg-tool">
        <span class="msg-tool-dot"></span>
        {{ toolLabel }}
        <!-- 静默期常见 60~100s，超过 2s 就把秒数亮出来，让用户知道还在跑 -->
        <span v-if="elapsed > 2" class="msg-tool-elapsed">已 {{ formatElapsed(elapsed) }}</span>
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
      <!-- 结构化载荷卡片：目前只有简历，kind 为其它值时忽略，老消息没有 meta 不受影响 -->
      <ResumeCard v-if="message.meta && message.meta.kind === 'resume'" :meta="message.meta" />
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
