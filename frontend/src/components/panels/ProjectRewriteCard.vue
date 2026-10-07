<script setup>
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { ArrowDown, Close, CopyDocument, Download } from '@element-plus/icons-vue'
import { exportApi, notifyError } from '../../api'
import { copyText, downloadMarkdown } from '../../utils/clipboard'
import { useAnalysisStore } from '../../stores/analysis'

const analysis = useAnalysisStore()
const collapsed = ref(false)

const rw = computed(() => analysis.rewrite || {})
const BLOCKS = [
  { key: 'improved_version', title: '通用优化版' },
  { key: 'python_backend_version', title: 'Python 后端版' },
  { key: 'agent_version', title: 'Agent / AI 版' },
]

async function onCopy(text) {
  const ok = await copyText(text)
  ElMessage.success(ok ? '已复制' : '复制失败')
}

async function onExport() {
  try {
    const res = await exportApi.projectRewrite(analysis.rewrite)
    downloadMarkdown('项目经历优化', res.data.markdown)
  } catch (err) {
    notifyError(err)
  }
}
</script>

<template>
  <div class="panel">
    <div class="panel-header">
      <span>项目经历优化</span>
      <div class="actions">
        <el-button size="small" text @click="onExport"><el-icon><Download /></el-icon> 导出</el-button>
        <el-button size="small" text @click="collapsed = !collapsed"><el-icon><ArrowDown /></el-icon></el-button>
        <el-button size="small" text @click="analysis.closeRewrite()"><el-icon><Close /></el-icon></el-button>
      </div>
    </div>
    <div v-show="!collapsed" class="panel-body">
      <div v-if="rw.original_issues?.length" class="rewrite-block">
        <div class="block-title">原描述问题</div>
        <ul style="margin: 0; padding-left: 18px; font-size: 13px; line-height: 1.8">
          <li v-for="i in rw.original_issues" :key="i">{{ i }}</li>
        </ul>
      </div>
      <div v-for="b in BLOCKS" :key="b.key" class="rewrite-block">
        <div class="block-title" style="display: flex; justify-content: space-between; align-items: center">
          <span>{{ b.title }}</span>
          <el-button size="small" text @click="onCopy(rw[b.key])"><el-icon><CopyDocument /></el-icon> 复制</el-button>
        </div>
        <div class="content">{{ rw[b.key] }}</div>
      </div>
    </div>
  </div>
</template>
