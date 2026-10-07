<script setup>
import * as echarts from 'echarts'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps({ dimensions: { type: Object, default: null } })
const el = ref(null)
let chart = null

const INDICATORS = [
  { name: '技能匹配度', key: 'skill_match' },
  { name: '项目相关性', key: 'project_relevance' },
  { name: '表达质量', key: 'expression_quality' },
  { name: '岗位适配度', key: 'job_fitness' },
]

function render() {
  if (!chart || !props.dimensions) return
  chart.setOption({
    radar: {
      indicator: INDICATORS.map((i) => ({ name: i.name, max: 100 })),
      radius: '65%',
    },
    series: [
      {
        type: 'radar',
        data: [
          {
            value: INDICATORS.map((i) => props.dimensions[i.key] ?? 0),
            name: '匹配评分',
            areaStyle: { opacity: 0.25 },
          },
        ],
      },
    ],
  })
}

function onResize() {
  chart?.resize()
}

onMounted(() => {
  chart = echarts.init(el.value)
  render()
  window.addEventListener('resize', onResize)
})

watch(() => props.dimensions, render, { deep: true })

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  chart?.dispose()
})
</script>

<template>
  <div ref="el" class="radar-box"></div>
</template>
