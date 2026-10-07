<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { notifyError } from '../../api'
import { useAnalysisStore } from '../../stores/analysis'
import { useChatStore } from '../../stores/chat'
import { useConversationStore } from '../../stores/conversation'

const analysis = useAnalysisStore()
const chat = useChatStore()
const conv = useConversationStore()
const shotInput = ref(null)

async function onAnalyze() {
  try {
    await analysis.analyze()
    ElMessage.success('分析完成')
  } catch (err) {
    notifyError(err)
  }
}

function pickShot() {
  if (!conv.currentId) {
    notifyError(new Error('请先新建或选择一个会话'))
    return
  }
  shotInput.value.click()
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
    ElMessage.success('截图分析完成')
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
    <input ref="shotInput" type="file" accept="image/png,image/jpeg,image/webp" style="display: none" @change="onShotChange" />
    <div style="display: flex; gap: 8px; margin-top: 8px">
      <el-button type="primary" size="small" :loading="analysis.analyzing" @click="onAnalyze">开始分析</el-button>
      <el-button size="small" :loading="analysis.analyzing" @click="pickShot">📷 招聘截图分析</el-button>
    </div>
  </div>
</template>
