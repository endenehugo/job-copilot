<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { notifyError } from '../../api'
import { useChatStore } from '../../stores/chat'
import { useConversationStore } from '../../stores/conversation'

const chat = useChatStore()
const conv = useConversationStore()
const text = ref('')
const imgInput = ref(null)
const uploading = ref(false)

async function send() {
  const query = text.value
  if (!query.trim() && !chat.pendingImages.length) {
    ElMessage.warning('请输入问题或上传图片')
    return
  }
  text.value = ''
  try {
    await chat.sendStream(query)
  } catch (err) {
    notifyError(err)
  }
}

function onKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    send()
  }
}

function pickImage() {
  if (!conv.currentId) {
    notifyError(new Error('请先新建或选择一个会话'))
    return
  }
  imgInput.value.click()
}

const MAX_IMAGE_SIZE = 10 * 1024 * 1024 // 10MB

async function onImageChange(e) {
  const files = [...e.target.files]
  e.target.value = ''
  if (!files.length) return
  // B31：空文件/超限前端直接拦截，不发请求
  const valid = []
  for (const file of files) {
    if (file.size === 0) {
      ElMessage.warning(`「${file.name}」是空文件，已跳过`)
    } else if (file.size > MAX_IMAGE_SIZE) {
      ElMessage.warning(`「${file.name}」超过 10MB 上限，已跳过`)
    } else {
      valid.push(file)
    }
  }
  if (!valid.length) return
  uploading.value = true
  try {
    // 多图并行上传；单张失败只提示该张，不影响其余
    await Promise.all(
      valid.map((file) =>
        chat.uploadImage(file).catch((err) => {
          notifyError(err)
          return null
        })
      )
    )
  } finally {
    uploading.value = false
  }
}
</script>

<template>
  <div class="composer">
    <div class="composer-card">
      <div v-if="chat.pendingImages.length" class="composer-images">
        <div v-for="(img, i) in chat.pendingImages" :key="img.url" class="thumb">
          <img :src="img.url" alt="待发送图片" />
          <span class="remove" @click="chat.removeImage(i)">×</span>
        </div>
      </div>
      <el-input
        v-model="text"
        type="textarea"
        :autosize="{ minRows: 2, maxRows: 6 }"
        resize="none"
        placeholder="输入问题，Enter 发送，Shift+Enter 换行"
        @keydown="onKeydown"
      />
      <div class="composer-actions">
        <input ref="imgInput" type="file" accept="image/png,image/jpeg,image/webp" multiple style="display: none" @change="onImageChange" />
        <!-- 上传中禁用图片按钮，避免连点重复选图；发送也等上传结束 -->
        <el-button size="small" text :loading="uploading" :disabled="uploading" @click="pickImage">🖼 图片</el-button>
        <span class="hint">Enter 发送 · Shift+Enter 换行</span>
        <el-button v-if="chat.sending" size="small" @click="chat.stopStreaming()">停止生成</el-button>
        <el-button v-else type="primary" size="small" :disabled="uploading" @click="send">发送</el-button>
      </div>
    </div>
  </div>
</template>
