<script setup>
const props = defineProps({ sources: { type: Array, default: null }, verification: { type: Object, default: null } })
import { ref } from 'vue'

const open = ref(false)

// unknown = 验证没跑成或没执行。绝不能兜底成 low（绿），否则"未验证"会被
// 当成"已验证且可信"展示——界面会主动误导用户。
const RISK_CLASS = { low: 'risk-low', medium: 'risk-medium', high: 'risk-high', unknown: 'risk-unknown' }
const RISK_TEXT = { low: '幻觉风险：低', medium: '幻觉风险：中', high: '幻觉风险：高', unknown: '未验证' }

// 三种重排策略的 score 语义不同，分开表述，避免把序位当相关性分数
function fmtScore(s) {
  if (s.score_source === 'llm_listwise') return s.score ? `排序 ${s.score}` : '相关性 —'
  if (s.score_source === 'rrf') return typeof s.score === 'number' ? `融合分 ${s.score.toFixed(3)}` : '相关性 —'
  return typeof s.score === 'number' ? `相关性 ${s.score.toFixed(2)}` : '相关性 —'
}
</script>

<template>
  <div v-if="sources?.length || verification" class="source-panel">
    <div>
      <span class="source-toggle" @click="open = !open">
        {{ open ? '▾' : '▸' }} 参考来源（{{ sources?.length || 0 }}）
      </span>
      <span
        v-if="verification"
        :class="['risk-badge', RISK_CLASS[verification.hallucination_risk] || 'risk-unknown']"
        :title="verification.verification_detail || ''"
      >
        {{ RISK_TEXT[verification.hallucination_risk] || '未验证' }}
      </span>
    </div>
    <template v-if="open">
      <div v-for="(s, i) in sources" :key="i" class="source-item">
        <div class="source-head">
          <span class="source-name">[{{ i + 1 }}] {{ s.source_name }}</span>
          <span class="hint">{{ fmtScore(s) }}</span>
        </div>
        <div class="source-preview">{{ (s.content || '').slice(0, 200) }}…</div>
      </div>
      <div v-if="verification?.unsupported_claims?.length" class="unsupported">
        ⚠ 以下断言未在来源中找到支撑：{{ verification.unsupported_claims.join('；') }}
      </div>
    </template>
  </div>
</template>
