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

async function onImageChange(e) {
  const files = [...e.target.files]
  e.target.value = ''
  for (const file of files) {
    try {
      await chat.uploadImage(file)
    } catch (err) {
      notifyError(err)
    }
  }
}
</script>

<template>
  <div class="composer">
    <div v-if="chat.pendingImages.length" class="composer-images">
      <div v-for="(img, i) in chat.pendingImages" :key="img.url" class="thumb">
        <img :src="img.url" alt="待发送图片" />
        <span class="remove" @click="chat.removeImage(i)">×</span>
      </div>
    </div>
    <div class="composer-box">
      <el-input
        v-model="text"
        type="textarea"
        :rows="2"
        resize="none"
        placeholder="输入问题，Enter 发送，Shift+Enter 换行"
        @keydown="onKeydown"
      />
      <div style="display: flex; flex-direction: column; gap: 6px">
        <input ref="imgInput" type="file" accept="image/png,image/jpeg,image/webp" multiple style="display: none" @change="onImageChange" />
        <el-button size="small" @click="pickImage">图片</el-button>
        <el-button type="primary" size="small" :loading="chat.sending" @click="send">发送</el-button>
      </div>
    </div>
  </div>
</template>
