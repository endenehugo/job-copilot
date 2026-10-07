<script setup>
import { computed, ref } from 'vue'
import { ArrowDown, Close, Download } from '@element-plus/icons-vue'
import { jobApi, notifyError } from '../../api'
import { exportApi } from '../../api'
import { downloadMarkdown } from '../../utils/clipboard'
import { useAnalysisStore } from '../../stores/analysis'
import { useConversationStore } from '../../stores/conversation'
import RadarChart from './RadarChart.vue'
import DimensionBars from './DimensionBars.vue'

const analysis = useAnalysisStore()
const conv = useConversationStore()
const collapsed = ref(false)

const jd = computed(() => analysis.analysis?.jd_analysis || {})
const scoring = computed(() => analysis.analysis?.scoring || {})
const keywords = computed(() => jd.value.keywords || [])

async function onExport() {
  try {
    const latest = await jobApi.latest(conv.currentId)
    const res = await exportApi.analysis(latest.data.id)
    downloadMarkdown(`JD分析-${jd.value.job_role || '报告'}`, res.data.markdown)
  } catch (err) {
    notifyError(err)
  }
}
</script>

<template>
  <div class="panel">
    <div class="panel-header">
      <span>JD 分析报告</span>
      <div class="actions">
        <el-button size="small" text @click="onExport"><el-icon><Download /></el-icon> 导出</el-button>
        <el-button size="small" text @click="collapsed = !collapsed"><el-icon><ArrowDown /></el-icon></el-button>
        <el-button size="small" text @click="analysis.closeAnalysis()"><el-icon><Close /></el-icon></el-button>
      </div>
    </div>
    <div v-show="!collapsed" class="panel-body">
      <div class="score-top">
        <div class="score-total">
          <div class="num">{{ scoring.total_score ?? '—' }}</div>
          <div class="label">匹配总分</div>
        </div>
        <div style="flex: 1; min-width: 220px">
          <div class="score-role">{{ jd.job_role || '未识别岗位' }}</div>
          <div class="keywords">
            <el-tag v-for="k in keywords" :key="k" size="small" effect="plain">{{ k }}</el-tag>
          </div>
        </div>
      </div>
      <div class="score-detail">
        <RadarChart :dimensions="scoring.dimensions" />
        <DimensionBars :dimensions="scoring.dimensions" />
      </div>
      <div v-if="scoring.strengths?.length" class="sg-section">
        <div class="sg-title">✅ 优势</div>
        <ul><li v-for="s in scoring.strengths" :key="s">{{ s }}</li></ul>
      </div>
      <div v-if="scoring.gaps?.length" class="sg-section">
        <div class="sg-title">⚠️ 缺口</div>
        <ul><li v-for="g in scoring.gaps" :key="g">{{ g }}</li></ul>
      </div>
      <div v-if="scoring.suggestions?.length" class="sg-section">
        <div class="sg-title">💡 建议</div>
        <ul><li v-for="s in scoring.suggestions" :key="s">{{ s }}</li></ul>
      </div>
      <div v-if="analysis.analysis?.extracted_jd_preview" class="sg-section">
        <div class="sg-title">📄 截图识别的 JD 预览</div>
        <div class="rewrite-block"><div class="content">{{ analysis.analysis.extracted_jd_preview }}</div></div>
      </div>
    </div>
  </div>
</template>
