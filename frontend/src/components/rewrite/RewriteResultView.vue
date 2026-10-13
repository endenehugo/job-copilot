<script setup>
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { exportApi, notifyError } from '../../api'
import { downloadMarkdown } from '../../utils/clipboard'
import { useAnalysisStore } from '../../stores/analysis'
import { useConversationStore } from '../../stores/conversation'

// 主区项目优化视图：点击会话树的优化子节点后展示全量结果。
// 数据源为 /resume/project/rewrite/detail，字段与旧版优化响应同构。
const analysis = useAnalysisStore()
const conv = useConversationStore()
const copied = ref('')
const exporting = ref(false)

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

function fmt(ts) {
  return (ts || '').replace('T', ' ').slice(0, 16)
}
</script>

<template>
  <div class="rview">
    <div class="rview-scroll">
      <header class="rview-head">
        <span>
          ✏️ 项目优化回看
          <span class="time-hint">{{ fmt(analysis.rewrite?.created_at) }}</span>
        </span>
        <span class="head-actions">
          <el-button size="small" :loading="exporting" @click="onExport">导出</el-button>
          <el-button size="small" @click="conv.backToConversation()">返回会话</el-button>
        </span>
      </header>

      <template v-if="analysis.rewrite">
        <div v-if="analysis.rewrite.original_description" class="rview-original">
          <div class="block-title">原描述</div>
          <div class="original-text">{{ analysis.rewrite.original_description }}</div>
        </div>

        <div v-if="issues.length" class="rview-issues">
          <div class="block-title warn">原描述问题</div>
          <ul class="rw-list"><li v-for="(i, idx) in issues" :key="idx">{{ i }}</li></ul>
        </div>

        <div v-if="versions.length">
          <div v-for="v in versions" :key="v.key" class="rw-block">
            <div class="rw-version-head">
              <span class="block-title">{{ v.label }}</span>
              <el-button size="small" text @click="copy(v.text, v.key)">
                {{ copied === v.key ? '已复制' : '复制' }}
              </el-button>
            </div>
            <div class="rw-text">{{ v.text }}</div>
          </div>
        </div>

        <div v-else-if="!issues.length" class="hint">该记录没有可展示的优化内容</div>
      </template>

      <div v-else class="hint" style="padding: 24px">优化数据加载中，请稍候…</div>
    </div>
  </div>
</template>

<style scoped>
.rview {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  max-width: 860px;
  width: 100%;
  margin: 0 auto;
}
.rview-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 16px 8px;
}
.rview-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 14px;
  font-weight: 600;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--border, #e5e6eb);
  margin-bottom: 14px;
}
.head-actions {
  display: flex;
  gap: 8px;
}
.time-hint {
  font-weight: 400;
  font-size: 12px;
  color: var(--text-secondary, #909399);
  margin-left: 8px;
}
.block-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}
.block-title.warn {
  color: var(--el-color-warning);
  font-size: 12px;
}
.rview-original {
  margin-bottom: 12px;
}
.original-text {
  margin-top: 4px;
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
  background: var(--el-fill-color-light);
  border-radius: 6px;
  padding: 10px 12px;
}
.rview-issues {
  margin-bottom: 12px;
}
.rw-list {
  margin: 4px 0 0;
  padding-left: 16px;
  font-size: 13px;
  line-height: 1.8;
  color: var(--el-text-color-regular);
}
.rw-block {
  margin-bottom: 14px;
}
.rw-version-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}
.rw-text {
  font-size: 13px;
  line-height: 1.8;
  white-space: pre-wrap;
  color: var(--el-text-color-regular);
  background: var(--el-fill-color-light);
  border-radius: 6px;
  padding: 10px 12px;
}
</style>
