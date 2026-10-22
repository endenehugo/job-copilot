<script setup>
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { exportApi, notifyError } from '../../api'
import { downloadMarkdown } from '../../utils/clipboard'
import { useAnalysisStore } from '../../stores/analysis'
import { useChatStore } from '../../stores/chat'
import { useConversationStore } from '../../stores/conversation'

// 主区 JD 分析视图：点击会话树的分析子节点后展示全量结果。
// 数据源为 /job/analysis/detail，与 latest 同构（扁平结构），复用旧 JdPanel 的渲染逻辑。
const analysis = useAnalysisStore()
const chat = useChatStore()
const conv = useConversationStore()
const exporting = ref(false)

const DIMENSION_LABELS = {
  skill_match: '技能匹配',
  project_relevance: '项目相关度',
  expression_quality: '表达质量',
  job_fitness: '岗位契合度',
}

const jd = computed(() => {
  const raw = analysis.analysis
  if (!raw) return null
  const src = raw.jd_analysis || raw
  return {
    jobRole: src.job_role || '',
    keywords: src.keywords || [],
    requirements: src.requirements || [],
    bonusPoints: src.bonus_points || [],
    angles: src.suggested_project_angles || [],
    scoring: raw.scoring || (raw.total_score == null ? null : {
      total_score: raw.total_score,
      dimensions: raw.dimensions || {},
      strengths: raw.strengths || [],
      gaps: raw.gaps || [],
      suggestions: raw.suggestions || [],
      warnings: raw.warnings || [],
    }),
  }
})

const dimensions = computed(() => {
  const d = jd.value?.scoring?.dimensions || {}
  return Object.entries(DIMENSION_LABELS)
    .filter(([key]) => typeof d[key] === 'number')
    .map(([key, label]) => ({ key, label, value: d[key] }))
})

const hasDetail = computed(() => {
  const v = jd.value
  if (!v) return false
  return Boolean(
    v.requirements.length || v.bonusPoints.length || v.angles.length ||
    v.scoring?.strengths?.length || v.scoring?.gaps?.length || v.scoring?.suggestions?.length
  )
})

async function onExport() {
  exporting.value = true
  try {
    let aid = analysis.analysis?.analysis_id ?? analysis.analysis?.id
    if (!aid) throw new Error('找不到可导出的分析记录')
    const res = await exportApi.analysis(aid)
    downloadMarkdown(`JD分析_${jd.value?.jobRole || '未命名岗位'}`, res.data.markdown)
    ElMessage.success('已导出 Markdown')
  } catch (err) {
    notifyError(err)
  } finally {
    exporting.value = false
  }
}

function fmt(ts) {
  return (ts || '').replace('T', ' ').slice(0, 16)
}

// 分数只够"快速扫描"，值不值得投要靠证据矩阵。这里把 JD 原文一起带进聊天，
// 不依赖检索（粘贴的 JD 不一定进了会话文档库），用户按回车前还能改。
function onDeepAnalyze() {
  const jdText = (analysis.analysis?.jd_text || '').trim()
  const text = jdText
    ? `用 job-match 判断这个岗位值不值得投。\n\nJD 原文：\n${jdText}`
    : '用 job-match 判断这个岗位值不值得投。'
  chat.presetComposer({ skill: 'job-match', text })
  conv.backToConversation()
}
</script>

<template>
  <div class="aview">
    <div class="aview-scroll">
      <header class="aview-head">
        <span>
          📊 JD 分析回看 · {{ jd?.jobRole || '岗位未识别' }}
          <span class="time-hint">{{ fmt(analysis.analysis?.created_at) }}</span>
        </span>
        <span class="head-actions">
          <el-button size="small" type="primary" plain @click="onDeepAnalyze">用 job-match 深入判断</el-button>
          <el-button size="small" :loading="exporting" @click="onExport">导出</el-button>
          <el-button size="small" @click="conv.backToConversation()">返回会话</el-button>
        </span>
      </header>

      <div v-if="jd" class="aview-body">
        <div class="score-row">
          <span v-if="jd.scoring?.total_score != null" class="jd-score">{{ jd.scoring.total_score }}<small>分</small></span>
          <span v-else-if="jd.scoring" class="jd-score jd-score-unknown">未评分</span>
          <div v-if="jd.scoring?.warnings?.length" class="jd-warn">⚠ {{ jd.scoring.warnings.join('；') }}</div>
        </div>

        <div v-if="dimensions.length" class="jd-dims">
          <div v-for="d in dimensions" :key="d.key" class="jd-dim">
            <span class="jd-dim-label">{{ d.label }}</span>
            <el-progress :percentage="d.value" :stroke-width="8" :show-text="false" />
            <span class="jd-dim-value">{{ d.value }}</span>
          </div>
        </div>

        <div v-if="jd.keywords.length" class="jd-tags">
          <el-tag v-for="k in jd.keywords" :key="k" type="info">{{ k }}</el-tag>
        </div>

        <el-collapse v-if="hasDetail" class="jd-collapse">
          <el-collapse-item v-if="jd.requirements.length" title="硬性要求" name="req">
            <ul class="jd-list"><li v-for="r in jd.requirements" :key="r">{{ r }}</li></ul>
          </el-collapse-item>
          <el-collapse-item v-if="jd.bonusPoints.length" title="加分项" name="bonus">
            <ul class="jd-list"><li v-for="b in jd.bonusPoints" :key="b">{{ b }}</li></ul>
          </el-collapse-item>
          <el-collapse-item v-if="jd.scoring?.strengths?.length" title="你的优势" name="strengths">
            <ul class="jd-list"><li v-for="s in jd.scoring.strengths" :key="s">{{ s }}</li></ul>
          </el-collapse-item>
          <el-collapse-item v-if="jd.scoring?.gaps?.length" title="待补缺口" name="gaps">
            <ul class="jd-list"><li v-for="g in jd.scoring.gaps" :key="g">{{ g }}</li></ul>
          </el-collapse-item>
          <el-collapse-item v-if="jd.scoring?.suggestions?.length" title="优化建议" name="suggestions">
            <ul class="jd-list"><li v-for="s in jd.scoring.suggestions" :key="s">{{ s }}</li></ul>
          </el-collapse-item>
          <el-collapse-item v-if="jd.angles.length" title="建议切入点" name="angles">
            <ul class="jd-list"><li v-for="a in jd.angles" :key="a">{{ a }}</li></ul>
          </el-collapse-item>
        </el-collapse>

        <div v-else-if="!jd.keywords.length" class="hint">未识别出结构化要点</div>

        <div v-if="analysis.analysis?.jd_text" class="jd-source">
          <div class="jd-source-title">分析的 JD 原文</div>
          <div class="jd-source-text">{{ analysis.analysis.jd_text }}</div>
        </div>
      </div>

      <div v-else class="hint" style="padding: 24px">分析数据加载中，请稍候…</div>
    </div>
  </div>
</template>

<style scoped>
.aview {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  max-width: 860px;
  width: 100%;
  margin: 0 auto;
}
.aview-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 16px 8px;
}
.aview-head {
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
.aview-body {
  font-size: 13px;
}
.score-row {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 10px;
}
.jd-score {
  font-size: 30px;
  font-weight: 500;
  color: var(--el-color-primary);
}
.jd-score small {
  font-size: 13px;
  margin-left: 2px;
}
.jd-score-unknown {
  font-size: 15px;
  font-weight: 400;
  color: var(--el-text-color-secondary);
}
.jd-warn {
  font-size: 12px;
  color: var(--el-color-warning);
  line-height: 1.5;
}
.jd-dims {
  max-width: 480px;
  margin-bottom: 12px;
}
.jd-dim {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.jd-dim-label {
  width: 62px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.jd-dim :deep(.el-progress) {
  flex: 1;
}
.jd-dim-value {
  width: 24px;
  text-align: right;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.jd-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 12px;
}
.jd-collapse {
  margin-bottom: 12px;
}
.jd-list {
  margin: 0;
  padding-left: 16px;
  font-size: 13px;
  line-height: 1.8;
  color: var(--el-text-color-regular);
}
.jd-source {
  border-top: 1px dashed var(--border, #e5e6eb);
  padding-top: 10px;
}
.jd-source-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  margin-bottom: 4px;
}
.jd-source-text {
  font-size: 12px;
  line-height: 1.7;
  color: var(--el-text-color-regular);
  white-space: pre-wrap;
  background: var(--el-fill-color-light);
  border-radius: 6px;
  padding: 10px 12px;
  max-height: 240px;
  overflow-y: auto;
}
</style>
