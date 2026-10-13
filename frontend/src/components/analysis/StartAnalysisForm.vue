<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { notifyError } from '../../api'
import { useAnalysisStore } from '../../stores/analysis'
import { useChatStore } from '../../stores/chat'
import { useConversationStore } from '../../stores/conversation'

// 发起 JD 分析的表单（发起对话框的"分析"标签页）。
// 前端先校验简历，避免点了才被后端拒绝；分析完成后主区自动切到结果视图。
const analysis = useAnalysisStore()
const chat = useChatStore()
const conv = useConversationStore()
const shotInput = ref(null)

const emit = defineEmits(['started'])

async function onAnalyze() {
  if (!conv.currentId) {
    ElMessage.warning('请先新建或选择一个会话')
    return
  }
  if (!conv.documents.length) {
    ElMessage.warning('当前会话还没有简历：请先上传简历，再进行 JD 分析')
    return
  }
  try {
    await analysis.analyze()
    ElMessage.success('分析完成，结果已保存')
    emit('started')
  } catch (err) {
    notifyError(err)
  }
}

function pickShot() {
  if (!conv.currentId) {
    notifyError(new Error('请先新建或选择一个会话'))
    return
  }
  if (!conv.documents.length) {
    ElMessage.warning('当前会话还没有简历：请先上传简历，再进行 JD 分析')
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
    emit('started')
  } catch (err) {
    notifyError(err)
  }
}
</script>

<template>
  <div class="start-form">
    <el-input
      v-model="analysis.jdText"
      type="textarea"
      :rows="5"
      placeholder="粘贴职位描述（JD）文本… 开始分析后将自动抽取关键词并结合简历评分"
    />
    <input
      ref="shotInput"
      type="file"
      accept="image/png,image/jpeg,image/webp"
      style="display: none"
      @change="onShotChange"
    />
    <div class="start-actions">
      <el-button type="primary" size="small" :loading="analysis.analyzing" @click="onAnalyze">开始分析</el-button>
      <el-button size="small" :loading="analysis.analyzing" @click="pickShot">📷 招聘截图分析</el-button>
      <span class="hint">需要会话中已有简历</span>
    </div>
  </div>
</template>

<style scoped>
.start-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}
</style>
