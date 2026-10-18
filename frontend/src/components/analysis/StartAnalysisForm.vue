<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { notifyError } from '../../api'
import { useAnalysisStore } from '../../stores/analysis'
import { useChatStore } from '../../stores/chat'
import { useConversationStore } from '../../stores/conversation'

// 发起 JD 分析的表单（发起对话框的"分析"标签页）。
// 前端先校验简历，避免点了才被后端拒绝；分析完成后主区自动切到结果视图。
const analysis = useAnalysisStore()
const chat = useChatStore()
const conv = useConversationStore()
const shotInput = ref(null)

const emit = defineEmits(['started'])

// 进度条的两段信息：
//   1) 阶段与百分比来自后端 stage 事件（真实，不是估算）；
//   2) 阶段内部是单次 LLM 调用，几秒内不会再有事件——所以除了数秒数，还要
//      在当前阶段区间内做有上限的缓动（最多 +8，且永不超过 96），
//      否则 68% 会一动不动停 4 秒，看着像卡死。
// 阶段名始终是后端给的当前真实阶段，缓动只影响条的长度。
const CREEP_CAP = 8
const clock = ref(Date.now())
const shown = ref(0)
const stageBase = ref(0)
let ticker = null

function tick() {
  clock.value = Date.now()
  const target = Math.min(96, stageBase.value + CREEP_CAP)
  const gap = target - shown.value
  if (gap > 0.05) shown.value = Math.min(target, shown.value + Math.max(gap * 0.06, 0.06))
}

watch(
  () => analysis.analyzing,
  (running) => {
    if (ticker) {
      clearInterval(ticker)
      ticker = null
    }
    if (!running) {
      shown.value = 0
      stageBase.value = 0
      return
    }
    clock.value = Date.now()
    shown.value = 0
    stageBase.value = 0
    ticker = setInterval(tick, 120)
  }
)
// 新阶段到达：条长只进不退，然后从该阶段的真实百分比继续缓动。
// 监听整个 progress 对象而不是 progress.stage —— 初始阶段与后端第一个阶段可能是同一个
// stage 名（都是 jd_parse），按名字监听会漏掉第一次真实进度。store 每次都换新对象。
watch(
  () => analysis.progress,
  (p) => {
    if (!p) return
    const base = Math.max(0, Math.min(99, p.percent ?? 0))
    stageBase.value = base
    shown.value = Math.max(shown.value, base)
  }
)
onUnmounted(() => {
  if (ticker) clearInterval(ticker)
})

const percent = computed(() => Math.round(Math.min(99, Math.max(0, shown.value))))
const stageLabel = computed(() => analysis.progress?.label || '分析中')
const elapsed = computed(() => {
  const started = analysis.progress?.startedAt
  return started ? ((clock.value - started) / 1000).toFixed(1) : '0.0'
})

async function onAnalyze() {
  if (!conv.currentId) {
    ElMessage.warning('请先新建或选择一个会话')
    return
  }
  if (!conv.documents.length) {
    ElMessage.warning('当前会话还没有简历：请先上传简历，再进行 JD 分析')
    return
  }
  try {
    await analysis.analyze()
    ElMessage.success('分析完成，结果已保存')
    emit('started')
  } catch (err) {
    notifyError(err)
  }
}

function pickShot() {
  if (!conv.currentId) {
    notifyError(new Error('请先新建或选择一个会话'))
    return
  }
  if (!conv.documents.length) {
    ElMessage.warning('当前会话还没有简历：请先上传简历，再进行 JD 分析')
    return
  }
  shotInput.value?.click()
}

async function onShotChange(e) {
  const file = e.target.files[0]
  e.target.value = ''
  if (!file) return
  try {
    await chat.uploadImage(file)
    const imageUrl = chat.pendingImages[chat.pendingImages.length - 1].url
    await analysis.analyzeScreenshot(imageUrl)
    chat.pendingImages = []
    ElMessage.success('截图分析完成，结果已保存')
    emit('started')
  } catch (err) {
    notifyError(err)
  }
}
</script>

<template>
  <div class="start-form">
    <el-input
      v-model="analysis.jdText"
      type="textarea"
      :rows="6"
      placeholder="粘贴职位描述（JD）文本… 开始分析后将自动抽取关键词并结合简历评分"
      @input="analysis.setJdText(conv.currentId, $event)"
    />

    <!-- 进度条：阶段名 + 真实百分比 + 已用时间。百分比来自后端 stage 事件，不是估算动画 -->
    <div
      v-if="analysis.analyzing"
      class="jd-progress"
      role="progressbar"
      :aria-valuenow="percent"
      aria-valuemin="0"
      aria-valuemax="100"
      :aria-label="stageLabel"
    >
      <div class="jd-progress-head">
        <span class="jd-progress-label">{{ stageLabel }}</span>
        <span class="jd-progress-meta">{{ percent }}% · {{ elapsed }}s</span>
      </div>
      <div class="jd-progress-track">
        <div class="jd-progress-fill" :style="{ width: `${percent}%` }" />
      </div>
    </div>

    <input
      ref="shotInput"
      type="file"
      accept="image/png,image/jpeg,image/webp"
      style="display: none"
      @change="onShotChange"
    />
    <div class="start-actions">
      <el-button type="primary" :loading="analysis.analyzing" @click="onAnalyze">开始分析</el-button>
      <el-button :loading="analysis.analyzing" @click="pickShot">招聘截图分析</el-button>
      <span class="hint">需要会话中已有简历</span>
    </div>
  </div>
</template>

<style scoped>
.jd-progress {
  margin-top: 12px;
}
.jd-progress-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 6px;
  font-size: 12.5px;
}
.jd-progress-label {
  color: var(--text-1);
}
.jd-progress-meta {
  color: var(--text-3);
  font-variant-numeric: tabular-nums;
}
.jd-progress-track {
  height: 6px;
  overflow: hidden;
  border-radius: 999px;
  background: var(--line);
}
.jd-progress-fill {
  position: relative;
  height: 100%;
  overflow: hidden;
  border-radius: inherit;
  background: var(--text-1);
  transition: width 0.45s cubic-bezier(0.4, 0, 0.2, 1);
}
/* 阶段之间是模型调用，百分比会停留几秒：流光表示"还在跑"，但不伪造进度 */
.jd-progress-fill::after {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.5), transparent);
  animation: jd-progress-shimmer 1.4s linear infinite;
}
@keyframes jd-progress-shimmer {
  from {
    transform: translateX(-100%);
  }
  to {
    transform: translateX(100%);
  }
}
@media (prefers-reduced-motion: reduce) {
  .jd-progress-fill {
    transition: none;
  }
  .jd-progress-fill::after {
    animation: none;
  }
}
.start-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 14px;
}
</style>

