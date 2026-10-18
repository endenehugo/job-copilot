<script setup>
import { computed } from 'vue'
import { useModelStore } from '../../stores/model'
import { useAuthStore } from '../../stores/auth'

const modelStore = useModelStore()
const auth = useAuthStore()

const visible = computed(() => modelStore.models.length > 0)

function onChange(model) {
  modelStore.setCurrent(model)
}
</script>

<template>
  <el-select
    v-if="visible"
    :model-value="modelStore.current"
    size="small"
    class="model-select"
    :title="auth.isAdmin ? '模型（管理员可选全部）' : '模型（普通用户可选 flash/plus）'"
    @change="onChange"
  >
    <el-option v-for="m in modelStore.models" :key="m" :label="m" :value="m" />
  </el-select>
</template>

<style scoped>
/* 顶栏里的模型选择器：去掉 EP 的描边与白底，做成纯文本控件 */
.model-select {
  width: 168px;
}
.model-select :deep(.el-select__wrapper) {
  min-height: 30px;
  padding: 0 8px;
  border-radius: 8px;
  background: transparent;
  box-shadow: none;
  font-size: 13px;
}
.model-select :deep(.el-select__wrapper:hover) {
  background: var(--surface-hover);
}
.model-select :deep(.el-select__wrapper.is-focused) {
  background: var(--surface-hover);
  box-shadow: none;
}
.model-select :deep(.el-select__selected-item) {
  color: var(--text-2);
  font-size: 13px;
}
.model-select :deep(.el-select__caret) {
  color: var(--text-3);
}
</style>
