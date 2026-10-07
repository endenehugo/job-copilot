<script setup>
import { onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { notifyError } from '../../api'
import { useKnowledgeStore } from '../../stores/knowledge'

const kb = useKnowledgeStore()

onMounted(() => kb.loadStatus())

async function onRebuild() {
  try {
    await kb.rebuild()
    ElMessage.success('重建完成')
  } catch (err) {
    notifyError(err)
  }
}

async function onSearch() {
  try {
    await kb.search()
  } catch (err) {
    notifyError(err)
  }
}
</script>

<template>
  <div>
    <div style="display: flex; gap: 8px; align-items: center; margin-bottom: 8px">
      <el-button size="small" :loading="kb.rebuilding" @click="onRebuild">重建索引</el-button>
      <span v-if="kb.status" class="hint">
        {{ kb.status.index_exists ? `已建索引 · ${kb.status.total_documents} 条` : '未建索引' }}
      </span>
    </div>
    <div style="display: flex; gap: 6px">
      <el-input v-model="kb.query" size="small" placeholder="搜索面试知识…" @keyup.enter="onSearch" />
      <el-button size="small" type="primary" :loading="kb.searching" @click="onSearch">搜索</el-button>
    </div>
    <div v-for="(r, i) in kb.results" :key="i" class="kb-result">
      <div>
        <span class="kb-title">{{ r.title }}</span>
        <span class="kb-cat">{{ r.category }}</span>
      </div>
      <div class="kb-content">{{ r.content.slice(0, 120) }}…</div>
    </div>
  </div>
</template>
