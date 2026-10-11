<script setup>
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { exportApi, jobApi, notifyError } from '../../api'
import { downloadMarkdown } from '../../utils/clipboard'
import { useAnalysisStore } from '../../stores/analysis'
import { useChatStore } from '../../stores/chat'
import { useConversationStore } from '../../stores/conversation'

const analysis = useAnalysisStore()
const chat = useChatStore()
const conv = useConversationStore()
const shotInput = ref(null)
const exporting = ref(false)

const DIMENSION_LABELS = {
  skill_match: '技能匹配',
  project_relevance: '项目相关度',
  expression_quality: '表达质量',
  job_fitness: '岗位契合度',
}

// /job/analyze 返回 {jd_analysis, scoring}；/job/analysis/latest 返回扁平结构。
// 两种都要能渲染，否则历史分析结果会显示不出来。
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

async function onAnalyze() {
  try {
    await analysis.analyze()
    ElMessage.success('分析完成，结果已保存')
  } catch (err) {
    notifyError(err)
  }
}

async function onExport() {
  exporting.value = true
  try {
    // /job/analyze 响应带 analysis_id；历史记录里字段叫 id
    let aid = analysis.analysis?.analysis_id ?? analysis.analysis?.id
    if (!aid && conv.currentId) {
      // 兜底：拉一次最新分析取 id
      const latest = await jobApi.latest(conv.currentId)
      aid = latest.data?.id
    }
    if (!aid) throw new Error('找不到可导出的分析记录，请先完成一次 JD 分析')
    const res = await exportApi.analysis(aid)
    downloadMarkdown(`JD分析_${jd.value?.jobRole || '未命名岗位'}`, res.data.markdown)
    ElMessage.success('已导出 Markdown')
  } catch (err) {
    notifyError(err)
  } finally {
    exporting.value = false
  }
}

function pickShot() {
  if (!conv.currentId) {
    notifyError(new Error('请先新建或选择一个会话'))
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
  } catch (err) {
    notifyError(err)
  }
}
</script>

<template>
  <div>
    <el-input
      v-model="analysis.jdText"
      type="textarea"
      :rows="4"
      placeholder="粘贴职位描述（JD）文本… 开始分析后将自动抽取关键词并结合简历评分"
    />
    <input
      ref="shotInput"
      type="file"
      accept="image/png,image/jpeg,image/webp"
      style="display: none"
      @change="onShotChange"
    />
    <div style="display: flex; gap: 8px; margin-top: 8px">
      <el-button type="primary" size="small" :loading="analysis.analyzing" @click="onAnalyze">开始分析</el-button>
      <el-button size="small" :loading="analysis.analyzing" @click="pickShot">📷 招聘截图分析</el-button>
      <el-button size="small" :loading="exporting" :disabled="!jd" @click="onExport">导出 MD</el-button>
    </div>

    <!-- B29：分析可达十几秒，按钮转圈在折叠面板里看不见，主区必须有意図性反馈 -->
    <div v-if="jd" v-loading="analysis.analyzing" class="jd-result" element-loading-text="正在分析 JD 与简历匹配度…">
      <div class="jd-head">
        <span class="jd-role">{{ jd.jobRole || '岗位未识别' }}</span>
        <span v-if="jd.scoring?.total_score != null" class="jd-score">{{ jd.scoring.total_score }}<small>分</small></span>
        <!-- 总分缺失时明确说"未评分"：留白会被当成加载失败，显示 0 分则是把"模型没返回"说成"你 0 分" -->
        <span v-else-if="jd.scoring" class="jd-score jd-score-unknown">未评分</span>
      </div>

      <div v-if="jd.scoring?.warnings?.length" class="jd-warn">
        ⚠ {{ jd.scoring.warnings.join('；') }}
      </div>

      <div v-if="dimensions.length" class="jd-dims">
        <div v-for="d in dimensions" :key="d.key" class="jd-dim">
          <span class="jd-dim-label">{{ d.label }}</span>
          <el-progress :percentage="d.value" :stroke-width="6" :show-text="false" />
          <span class="jd-dim-value">{{ d.value }}</span>
        </div>
      </div>

      <div v-if="jd.keywords.length" class="jd-tags">
        <el-tag v-for="k in jd.keywords" :key="k" size="small" type="info">{{ k }}</el-tag>
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

      <div v-else-if="!jd.keywords.length" class="jd-empty">未识别出结构化要点</div>
    </div>
  </div>
</template>

<style scoped>
.jd-result {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px solid var(--el-border-color-lighter);
}
.jd-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}
.jd-role {
  font-size: 13px;
  font-weight: 500;
  color: var(--el-text-color-primary);
}
.jd-score {
  font-size: 18px;
  font-weight: 500;
  color: var(--el-color-primary);
}
.jd-score small {
  font-size: 11px;
  margin-left: 1px;
}
/* 未评分：灰色，与蓝色真实分数区分开 */
.jd-score-unknown {
  font-size: 13px;
  font-weight: 400;
  color: var(--el-text-color-secondary);
}
.jd-warn {
  margin-top: 6px;
  font-size: 12px;
  color: var(--el-color-warning);
  line-height: 1.5;
}
.jd-dims {
  margin-top: 8px;
}
.jd-dim {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}
.jd-dim-label {
  width: 62px;
  font-size: 11px;
  color: var(--el-text-color-secondary);
}
.jd-dim :deep(.el-progress) {
  flex: 1;
}
.jd-dim-value {
  width: 22px;
  text-align: right;
  font-size: 11px;
  color: var(--el-text-color-secondary);
}
.jd-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 8px;
}
.jd-collapse {
  margin-top: 8px;
}
.jd-list {
  margin: 0;
  padding-left: 16px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--el-text-color-regular);
}
.jd-empty {
  margin-top: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
