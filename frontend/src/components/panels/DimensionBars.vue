<script setup>
const props = defineProps({ dimensions: { type: Object, default: null } })

const DIMS = [
  { key: 'skill_match', name: '技能匹配度', color: '#409eff' },
  { key: 'project_relevance', name: '项目相关性', color: '#67c23a' },
  { key: 'expression_quality', name: '表达质量', color: '#e6a23c' },
  { key: 'job_fitness', name: '岗位适配度', color: '#f56c6c' },
]
</script>

<template>
  <div class="dimension-list">
    <div v-for="d in DIMS" :key="d.key" class="dimension-row">
      <div class="dim-head">
        <span>{{ d.name }}</span>
        <span>{{ dimensions?.[d.key] ?? 0 }}</span>
      </div>
      <div class="dimension-bar-bg">
        <div class="dimension-bar-fill" :style="{ width: (dimensions?.[d.key] ?? 0) + '%', background: d.color }"></div>
      </div>
    </div>
  </div>
</template>
