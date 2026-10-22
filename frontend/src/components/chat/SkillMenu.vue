<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps({
  // 后端已按产品顺序排好，这里只平铺
  skills: { type: Array, default: () => [] },
  activeIndex: { type: Number, default: 0 },
})
const emit = defineEmits(['pick', 'close'])

const listEl = ref(null)

// 键盘选择时面板可能比可视区高，把高亮项滚进视野
watch(
  () => props.activeIndex,
  async (index) => {
    await nextTick()
    listEl.value?.querySelector(`[data-index="${index}"]`)?.scrollIntoView({ block: 'nearest' })
  }
)

// 焦点在 textarea 时 Esc 由 Composer 处理；从「+」菜单进来时焦点在按钮上，
// 这里兜一层全局监听（close 是幂等的）
function onKeydown(e) {
  if (e.key === 'Escape') emit('close')
}
onMounted(() => document.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => document.removeEventListener('keydown', onKeydown))
</script>

<template>
  <div class="skill-menu" role="listbox" aria-label="选择技能">
    <div class="skill-menu-head">
      <span class="skill-menu-title">选择技能</span>
      <span class="skill-menu-hint">↑↓ 选择 · Enter 确认</span>
    </div>

    <div ref="listEl" class="skill-menu-list">
      <template v-for="(item, i) in skills" :key="item.name">
        <div v-if="i === 0 || skills[i - 1].group !== item.group" class="skill-group">
          {{ item.group }}
        </div>
        <!-- mousedown 必须 prevent：否则 textarea 先失焦，面板会被 blur 关掉，
             点击等于没反应 -->
        <button
          class="skill-row"
          :class="{ 'is-active': i === activeIndex }"
          :data-index="i"
          role="option"
          :aria-selected="i === activeIndex"
          @mousedown.prevent="emit('pick', item)"
        >
          <span class="skill-row-title">{{ item.title }}</span>
          <span class="skill-row-desc">{{ item.summary }}</span>
        </button>
      </template>

      <div v-if="!skills.length" class="skill-empty">技能加载中…</div>
    </div>
  </div>
</template>

<style scoped>
.skill-menu {
  position: absolute;
  left: 0;
  bottom: calc(100% + 10px);
  width: 360px;
  max-height: 52vh;
  display: flex;
  flex-direction: column;
  padding: 6px;
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: 16px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.14);
  z-index: 60;
}
.skill-menu-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  padding: 6px 10px 8px;
}
.skill-menu-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-2);
}
.skill-menu-hint {
  font-size: 11px;
  color: var(--text-3);
}
.skill-menu-list {
  overflow-y: auto;
  overscroll-behavior: contain;
}
.skill-group {
  padding: 8px 10px 4px;
  font-size: 11px;
  color: var(--text-3);
  letter-spacing: 0.02em;
}
.skill-row {
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: 100%;
  padding: 8px 10px;
  border: 0;
  border-radius: 10px;
  background: transparent;
  text-align: left;
  cursor: pointer;
  font-family: inherit;
  color: var(--text-1);
}
.skill-row:hover,
.skill-row.is-active {
  background: var(--surface-hover);
}
.skill-row-title {
  font-size: 14px;
  line-height: 1.35;
}
.skill-row-desc {
  font-size: 12px;
  line-height: 1.4;
  color: var(--text-3);
}
.skill-empty {
  padding: 14px 10px;
  font-size: 12px;
  color: var(--text-3);
}
</style>
