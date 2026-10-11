<script setup>
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { exportApi, notifyError } from '../../api'
import { downloadMarkdown } from '../../utils/clipboard'
import { useAnalysisStore } from '../../stores/analysis'

const analysis = useAnalysisStore()
const copied = ref('')
const exporting = ref(false)

// 三个版本 + 原描述问题，之前算完只弹一句 toast，界面上无处可见
const versions = computed(() => {
  const r = analysis.rewrite
  if (!r) return []
  return [
    { key: 'improved', label: '通用优化版', text: r.improved_version },
    { key: 'python', label: 'Python 后端版', text: r.python_backend_version },
    { key: 'agent', label: 'Agent 方向版', text: r.agent_version },
  ].filter((v) => v.text && v.text.trim())
})

const issues = computed(() => {
  const list = analysis.rewrite?.original_issues || []
  return list.filter((i) => i && String(i).trim())
})

async function onRewrite() {
  try {
    await analysis.rewrite()
    ElMessage.success('优化完成，结果已保存')
  } catch (err) {
    notifyError(err)
  }
}

async function onExport() {
  if (!analysis.rewrite) return
  exporting.value = true
  try {
    const res = await exportApi.projectRewrite(analysis.rewrite)
    downloadMarkdown('项目经历优化', res.data.markdown)
    ElMessage.success('已导出 Markdown')
  } catch (err) {
    notifyError(err)
  } finally {
    exporting.value = false
  }
}

async function copy(text, key) {
  try {
    await navigator.clipboard.writeText(text)
    copied.value = key
    ElMessage.success('已复制')
    setTimeout(() => { if (copied.value === key) copied.value = '' }, 1500)
  } catch {
    notifyError(new Error('复制失败，请手动选中文本复制'))
  }
}
</script>

<template>
  <div>
    <el-input
      v-model="analysis.rewriteText"
      type="textarea"
      :rows="4"
      placeholder="粘贴需要优化的项目经历描述… 将结合会话内简历给出通用版、后端版与 Agent 版"
    />
    <div style="margin-top: 8px">
      <el-button type="primary" size="small" :loading="analysis.rewriting" @click="onRewrite">开始优化</el-button>
      <el-button size="small" :loading="exporting" :disabled="!analysis.rewrite" @click="onExport">导出 MD</el-button>
    </div>

    <!-- B29：优化是长操作，主区骨架反馈 -->
    <div v-if="analysis.rewrite" v-loading="analysis.rewriting" class="rw-result" element-loading-text="正在生成优化版本…">
      <div v-if="issues.length" class="rw-issues">
        <div class="rw-issues-title">原描述问题</div>
        <ul class="rw-list"><li v-for="(i, idx) in issues" :key="idx">{{ i }}</li></ul>
      </div>

      <div v-if="versions.length">
        <div v-for="v in versions" :key="v.key" class="rw-block">
          <div class="rw-head">
            <span>{{ v.label }}</span>
            <el-button size="small" text @click="copy(v.text, v.key)">
              {{ copied === v.key ? '已复制' : '复制' }}
            </el-button>
          </div>
          <div class="rw-text">{{ v.text }}</div>
        </div>
      </div>

      <div v-else class="rw-empty">未生成优化版本，可调整描述后重试</div>
    </div>
  </div>
</template>

<style scoped>
.rw-result {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px solid var(--el-border-color-lighter);
}
.rw-issues {
  margin-bottom: 8px;
}
.rw-issues-title {
  font-size: 12px;
  font-weight: 500;
  color: var(--el-color-warning);
  margin-bottom: 2px;
}
.rw-list {
  margin: 0;
  padding-left: 16px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--el-text-color-regular);
}
.rw-block {
  margin-bottom: 10px;
}
.rw-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
  font-weight: 500;
  color: var(--el-text-color-primary);
  margin-bottom: 3px;
}
.rw-text {
  font-size: 12px;
  line-height: 1.7;
  white-space: pre-wrap;
  color: var(--el-text-color-regular);
  background: var(--el-fill-color-light);
  border-radius: 4px;
  padding: 6px 8px;
  max-height: 220px;
  overflow-y: auto;
}
.rw-empty {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
