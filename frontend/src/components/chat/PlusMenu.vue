<script setup>
import { onBeforeUnmount, onMounted } from 'vue'
import AppIcon from '../shell/AppIcon.vue'

const emit = defineEmits(['close', 'pick-image', 'open-docs', 'task', 'open-kb'])

// 菜单只放真实能力：图片/简历上传、三类任务、资料库、知识库。
// "创作图像 / 网页搜索 / 深度研究"后端没有对应入口，不做。
const GROUPS = [
  [
    { icon: 'image', title: '添加照片和文件', desc: '图片、PDF / Word 简历均可', act: () => emit('pick-image') },
    { icon: 'folder', title: '从资料库添加', desc: '查看已上传文档与简历版本', act: () => emit('open-docs') },
  ],
  [
    { icon: 'target', title: '模拟面试', desc: '按方向进行多轮追问与评分', act: () => emit('task', 'interview') },
    { icon: 'chart', title: 'JD 分析', desc: '解析岗位要求并给出匹配评分', act: () => emit('task', 'analysis') },
    { icon: 'pen', title: '项目优化', desc: '把项目描述改写成简历亮点', act: () => emit('task', 'rewrite') },
  ],
  [{ icon: 'library', title: '知识库', desc: '管理题库与八股条目', act: () => emit('open-kb') }],
]

function pick(row) {
  row.act()
  emit('close')
}

function onKeydown(e) {
  if (e.key === 'Escape') emit('close')
}

onMounted(() => document.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => document.removeEventListener('keydown', onKeydown))
</script>

<template>
  <div class="plus-menu" role="menu">
    <template v-for="(group, gi) in GROUPS" :key="gi">
      <div v-if="gi > 0" class="plus-menu-line"></div>
      <button v-for="row in group" :key="row.title" class="plus-row" role="menuitem" @click="pick(row)">
        <AppIcon :name="row.icon" :size="18" class="plus-row-icon" />
        <span class="plus-row-text">
          <span class="plus-row-title">{{ row.title }}</span>
          <span class="plus-row-desc">{{ row.desc }}</span>
        </span>
      </button>
    </template>
  </div>
</template>

<style scoped>
.plus-menu {
  position: absolute;
  left: 0;
  bottom: calc(100% + 10px);
  width: 320px;
  padding: 6px;
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: 16px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.14);
  z-index: 60;
}
.plus-menu-line {
  height: 1px;
  background: var(--line);
  margin: 6px 8px;
}
.plus-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  width: 100%;
  padding: 9px 10px;
  border: 0;
  border-radius: 10px;
  background: transparent;
  text-align: left;
  cursor: pointer;
  font-family: inherit;
  color: var(--text-1);
}
.plus-row:hover {
  background: var(--surface-hover);
}
.plus-row-icon {
  margin-top: 2px;
  color: var(--text-2);
}
.plus-row-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.plus-row-title {
  font-size: 14px;
  line-height: 1.35;
}
.plus-row-desc {
  font-size: 12px;
  line-height: 1.35;
  color: var(--text-3);
}
</style>
