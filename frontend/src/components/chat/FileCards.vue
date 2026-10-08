<script setup>
import { computed } from 'vue'

const props = defineProps({
  files: { type: Array, default: () => [] },
})

const items = computed(() =>
  (props.files || [])
    .filter((f) => f && f.url)
    .filter((f, i, arr) => arr.findIndex((x) => x.url === f.url) === i)
    .map((f) => ({
      name: f.name || '下载文件',
      url: f.url,
      size: f.size ? formatSize(f.size) : '',
    }))
)

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
</script>

<template>
  <div v-if="items.length" class="file-cards">
    <div class="file-cards-title">本轮生成的文件</div>
    <a
      v-for="item in items"
      :key="item.url"
      class="file-card"
      :href="item.url"
      :download="item.name"
      target="_blank"
      rel="noopener"
    >
      <span class="file-ext">HTML</span>
      <span class="file-meta">
        <span class="file-name">{{ item.name }}</span>
        <span v-if="item.size" class="file-size">{{ item.size }}</span>
      </span>
      <span class="file-action">下载</span>
    </a>
  </div>
</template>

<style scoped>
.file-cards {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--border-color, #e5e7eb);
}
.file-cards-title {
  font-size: 12px;
  color: var(--text-secondary, #6b7280);
  margin-bottom: 6px;
}
.file-card {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  margin-bottom: 6px;
  border: 1px solid var(--border-color, #e5e7eb);
  border-radius: 8px;
  text-decoration: none;
  color: inherit;
  transition: border-color 0.15s ease;
}
.file-card:hover {
  border-color: var(--primary-color, #2458b8);
}
.file-ext {
  flex: none;
  font-size: 11px;
  font-weight: 500;
  padding: 2px 6px;
  border-radius: 4px;
  background: #eef2ff;
  color: #2458b8;
}
.file-meta {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.file-name {
  font-size: 13px;
  word-break: break-all;
}
.file-size {
  font-size: 11px;
  color: var(--text-secondary, #6b7280);
}
.file-action {
  flex: none;
  font-size: 12px;
  color: var(--primary-color, #2458b8);
}
</style>
