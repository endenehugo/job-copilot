<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useResumeGenStore } from '../../stores/resumeGen'
import { useConversationStore } from '../../stores/conversation'

// 主区简历预览：左（上）是 A4 预览，顶部是操作栏与风险提示。
// 预览用 iframe srcdoc 而不是 src 指向下载路由：/conversation/file/* 一律按
// attachment 下发（防托管内容在本站域内被渲染），塞进 iframe 会变成下载。
const gen = useResumeGenStore()
const conv = useConversationStore()

const frame = ref(null)
const frameHeight = ref(1123)

const warnings = computed(() => gen.warnings)
const missing = computed(() => gen.missing)
const stages = computed(() => gen.result?.stages || {})

// 打开的版本由 activeNodeId 决定：从消息卡片、侧栏树点进来都落到同一个入口，
// 刷新页面后仍是同一版（P1 时结果只在内存里，刷新即丢）。
async function syncFromRoute() {
  const id = conv.activeNodeId
  if (!id) return
  try {
    await gen.open(id)
  } catch {
    /* 错误提示已由 store 处理 */
  }
}
onMounted(syncFromRoute)
watch(() => conv.activeNodeId, syncFromRoute)

function onFrameLoad() {
  const doc = frame.value?.contentDocument
  if (!doc) return
  const height = Math.max(
    doc.documentElement?.scrollHeight || 0,
    doc.body?.scrollHeight || 0
  )
  if (height > 0) frameHeight.value = height
}

function onPrint() {
  const win = frame.value?.contentWindow
  if (!win) return
  win.focus()
  win.print()
}

function onDownload() {
  const url = gen.result?.html_url
  if (!url) return
  const link = document.createElement('a')
  link.href = url
  link.download = gen.result?.filename || 'resume.html'
  document.body.appendChild(link)
  link.click()
  link.remove()
}

async function onRegenerate() {
  try {
    const res = await gen.generate(conv.currentId)
    // 生成的是新一版，把当前节点指向它，否则下次刷新会回到旧版
    conv.selectNode('resume', conv.currentId, res.generation_id)
    ElMessage.success(`已生成第 ${res.version} 版`)
  } catch {
    /* 错误提示已由 store 处理 */
  }
}

// 补字段重渲染：0 次 LLM，只做字段赋值 + 重新套模板。
// 认不出的字段由后端返回 ignored_fields，不猜、不乱填。
const fillVisible = ref(false)
const fillValues = ref({})

function openFill() {
  const next = {}
  for (const label of missing.value) next[label] = ''
  fillValues.value = next
  fillVisible.value = true
}

async function onFillSubmit() {
  const extra = {}
  for (const [key, value] of Object.entries(fillValues.value)) {
    const v = String(value || '').trim()
    if (v) extra[key] = v
  }
  if (!Object.keys(extra).length) {
    ElMessage.warning('请至少填写一项')
    return
  }
  try {
    const res = await gen.regenerate(extra)
    fillVisible.value = false
    conv.selectNode('resume', conv.currentId, res.generation_id)
    const ignored = res.ignored_fields || []
    ElMessage.success(
      ignored.length
        ? `已生成第 ${res.version} 版；${ignored.join('、')} 需手动编辑`
        : `已生成第 ${res.version} 版`
    )
  } catch {
    /* 错误提示已由 store 处理 */
  }
}
</script>

<template>
  <div class="rgen">
    <header class="rgen-head">
      <span class="rgen-title">
        简历预览
        <span v-if="gen.version" class="version-chip">第 {{ gen.version }} 版</span>
        <span v-if="stages.total" class="time-hint">生成耗时 {{ stages.total }}s</span>
        <span v-else-if="gen.opening" class="time-hint">加载中…</span>
      </span>
      <span class="head-actions">
        <el-button size="small" :loading="gen.loading" @click="onRegenerate">重新生成一版</el-button>
        <el-button size="small" :disabled="!gen.hasResult" @click="onDownload">下载 HTML</el-button>
        <el-button size="small" type="primary" :disabled="!gen.hasResult" @click="onPrint">
          打印 / 存为 PDF
        </el-button>
        <el-button size="small" text @click="conv.backToConversation()">返回会话</el-button>
      </span>
    </header>

    <div class="rgen-scroll">
      <div v-if="warnings.length" class="notice notice--warn">
        <div class="notice-title">以下内容无法在简历原文中找到出处，投递前请逐条核对</div>
        <ul>
          <li v-for="(w, i) in warnings" :key="i">{{ w }}</li>
        </ul>
      </div>

      <div v-if="missing.length" class="notice notice--info">
        <div class="notice-title">素材里缺这些信息（正文已用【待补】标出）</div>
        <div class="notice-tags">
          <span v-for="(m, i) in missing" :key="i" class="tag">{{ m }}</span>
        </div>
        <el-button
          size="small"
          class="fill-btn"
          :loading="gen.regenerating"
          @click="openFill"
        >
          补上这些字段再出一版
        </el-button>
      </div>

      <div v-if="gen.hasResult" class="paper">
        <iframe
          ref="frame"
          class="paper-frame"
          :style="{ height: frameHeight + 'px' }"
          :srcdoc="gen.html"
          title="简历预览"
          @load="onFrameLoad"
        ></iframe>
      </div>
      <div v-else class="empty">
        <p>还没有生成简历。</p>
        <p class="empty-hint">
          从「发起任务 → 生成简历」开始；生成后会基于当前会话里的简历与 JD 分析产出 A4 简历。
        </p>
      </div>
    </div>

    <el-dialog v-model="fillVisible" title="补上缺失字段" width="440px">
      <div class="fill-form">
        <div v-for="(_, label) in fillValues" :key="label" class="fill-row">
          <span class="fill-label">{{ label }}</span>
          <el-input v-model="fillValues[label]" size="small" placeholder="留空则不填" />
        </div>
      </div>
      <p class="fill-tip">
        可自动填入：姓名、学校、专业、学历、毕业时间、手机、邮箱、城市、目标岗位。
        其余字段会在生成后提示「需手动编辑」。
      </p>
      <template #footer>
        <el-button size="small" @click="fillVisible = false">取消</el-button>
        <el-button size="small" type="primary" :loading="gen.regenerating" @click="onFillSubmit">
          生成新一版
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.rgen {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  width: 100%;
  max-width: 900px;
  margin: 0 auto;
}
.rgen-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 0 8px 10px;
  border-bottom: 1px solid var(--border, #e5e6eb);
  font-size: 14px;
  font-weight: 600;
}
.rgen-title {
  white-space: nowrap;
}
.time-hint {
  font-weight: 400;
  font-size: 12px;
  color: var(--text-secondary, #909399);
  margin-left: 8px;
}
.version-chip {
  margin-left: 8px;
  padding: 1px 7px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 500;
  color: var(--text-secondary, #6b7280);
  background: var(--surface-active, #f0f1f4);
}
.head-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.rgen-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 14px 8px 24px;
}

.notice {
  border-radius: 6px;
  padding: 10px 12px;
  margin-bottom: 12px;
  font-size: 13px;
  line-height: 1.6;
}
.notice--warn {
  background: #fef3c7;
  border: 1px solid #fcd34d;
  color: #92400e;
}
.notice--info {
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
  color: #334155;
}
.notice-title {
  font-weight: 600;
  margin-bottom: 6px;
}
.notice ul {
  margin: 0;
  padding-left: 18px;
}
.notice-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.tag {
  background: #fff;
  border: 1px solid #cbd5e1;
  border-radius: 4px;
  padding: 1px 6px;
  font-size: 12px;
}
.fill-btn {
  margin-top: 10px;
}
.fill-form {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.fill-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.fill-label {
  flex: none;
  width: 96px;
  font-size: 13px;
  color: var(--text-secondary, #4b5563);
  text-align: right;
}
.fill-tip {
  margin: 12px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-secondary, #909399);
}

/* 白纸：预览区底色浅灰，纸张带投影，模拟真实打印观感 */
.paper {
  background: #f5f6f8;
  border-radius: 8px;
  padding: 16px;
  display: flex;
  justify-content: center;
}
.paper-frame {
  width: 100%;
  max-width: 794px;
  border: 0;
  background: #fff;
  box-shadow: 0 2px 14px rgba(15, 23, 42, 0.12);
}

.empty {
  padding: 48px 16px;
  text-align: center;
  color: var(--text-secondary, #6b7280);
  font-size: 14px;
}
.empty-hint {
  font-size: 13px;
  color: var(--text-secondary, #9ca3af);
  margin-top: 8px;
}
</style>
