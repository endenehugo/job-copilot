<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { notifyError } from '../../api'
import { useChatStore } from '../../stores/chat'
import { useConversationStore } from '../../stores/conversation'
import { useResumeStore } from '../../stores/resume'
import { openStartDialog } from '../../utils/startDialog'
import AppIcon from '../shell/AppIcon.vue'
import PlusMenu from './PlusMenu.vue'

const props = defineProps({
  // hero：空态居中；docked：有消息后贴底。
  // 同一实例只换 CSS 位置，不能用 v-if 渲染两份——否则首条消息发出时
  // 组件重挂载，会丢输入内容、焦点和待发送图片。
  variant: { type: String, default: 'docked' },
})
const emit = defineEmits(['open-panel'])

const chat = useChatStore()
const conv = useConversationStore()
const resume = useResumeStore()

const text = ref('')
const fileInput = ref(null)
const uploading = ref(false)
const plusOpen = ref(false)
const plusWrap = ref(null)
const ta = ref(null)

// 图片进"待发附件"，简历文档进会话级资料库——同一个入口，按类型分流
const IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp']
const DOC_EXTS = /\.(pdf|docx|txt)$/i
const FILE_ACCEPT = 'image/png,image/jpeg,image/webp,.pdf,.docx,.txt'
const MAX_IMAGE_SIZE = 10 * 1024 * 1024 // 10MB
const MAX_DOC_SIZE = 20 * 1024 * 1024 // 20MB

const DOC_STATE = {
  uploading: '上传中',
  parsed: '索引中',
  pending: '索引中',
  indexed: '已加入资料库',
  failed: '上传失败',
}

function docState(doc) {
  return DOC_STATE[doc.status] || doc.status
}

const canSend = computed(() => Boolean(text.value.trim() || chat.pendingImages.length) && !uploading.value)

// 只读会话（演示样本）：服务端在列表项与详情上按当前身份算好了 readonly，
// 这里只做展示层降级——真正的拦截在后端，前端挡一道只是为了不让用户白点
const readonly = computed(() => conv.currentReadonly)

async function startOwn() {
  try {
    // 新建并选中：Workspace 的 watch 会跟着清空消息区与各模块状态
    await conv.create()
  } catch (err) {
    notifyError(err)
  }
}

function autosize() {
  const el = ta.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 200)}px`
}

async function send() {
  if (chat.sending) return
  const query = text.value
  if (!query.trim() && !chat.pendingImages.length) {
    ElMessage.warning('请输入问题或上传图片')
    return
  }
  text.value = ''
  await nextTick()
  autosize()
  try {
    await chat.sendStream(query)
  } catch (err) {
    notifyError(err)
  }
  ta.value?.focus()
}

function onKeydown(e) {
  if (e.key === 'Escape' && plusOpen.value) {
    plusOpen.value = false
    return
  }
  // 中文输入法：候选词确认的 Enter 带 isComposing（部分浏览器是 keyCode 229），
  // 不能当成"发送"，否则打"项目经理"时会先把半截话发出去。
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
    e.preventDefault()
    send()
  }
}

function togglePlus() {
  plusOpen.value = !plusOpen.value
}

function onDocMousedown(e) {
  if (!plusOpen.value) return
  if (plusWrap.value && !plusWrap.value.contains(e.target)) plusOpen.value = false
}

onMounted(() => {
  document.addEventListener('mousedown', onDocMousedown)
  autosize()
})
onBeforeUnmount(() => document.removeEventListener('mousedown', onDocMousedown))
watch(() => chat.pendingImages.length, () => nextTick(autosize))

function pickFiles() {
  fileInput.value.click()
}

async function onFilesChange(e) {
  const files = [...e.target.files]
  e.target.value = ''
  if (!files.length) return

  // 空文件/超限前端直接拦截，不发请求
  const images = []
  const docs = []
  for (const file of files) {
    if (IMAGE_TYPES.includes(file.type)) {
      if (file.size === 0) ElMessage.warning(`「${file.name}」是空文件，已跳过`)
      else if (file.size > MAX_IMAGE_SIZE) ElMessage.warning(`「${file.name}」超过 10MB 上限，已跳过`)
      else images.push(file)
    } else if (DOC_EXTS.test(file.name)) {
      if (file.size === 0) ElMessage.warning(`「${file.name}」是空文件，已跳过`)
      else if (file.size > MAX_DOC_SIZE) ElMessage.warning(`「${file.name}」超过 20MB 上限，已跳过`)
      else docs.push(file)
    } else {
      ElMessage.warning(`「${file.name}」格式不支持，可上传图片或 PDF / Word / txt`)
    }
  }
  if (!images.length && !docs.length) return

  uploading.value = true
  try {
    // 图片和文档都挂在会话下：空态直接传会 400，这里先建会话
    if (!conv.currentId) await conv.create()

    // 多图并行；单张失败只提示该张，不影响其余
    await Promise.all(images.map((file) => chat.uploadImage(file).catch((err) => notifyError(err))))

    // 简历串行：一次上传要解析并重建索引，并行只会互相抢 CPU
    let uploaded = 0
    for (const file of docs) {
      try {
        await chat.uploadDoc(file)
        uploaded += 1
      } catch (err) {
        notifyError(err, `「${file.name}」上传失败`)
      }
    }
    if (uploaded) {
      // 资料库抽屉与简历版本都读会话 detail，这里同步一次；
      // 正在流式生成时不刷新，避免把半截回答覆盖掉
      if (!chat.sending) await conv.refreshDetail().catch(() => {})
      resume.load(conv.currentId).catch(() => {})
    }
  } catch (err) {
    notifyError(err)
  } finally {
    uploading.value = false
    await nextTick()
    autosize()
  }
}

function onTask(tab) {
  openStartDialog(tab)
}
</script>

<template>
  <div class="composer" :class="`composer--${props.variant}`">
    <!-- 只读会话换成引导条而不是灰掉的输入框：
         灰输入框会让人反复尝试，一条"这是演示 + 去哪开始"更省事 -->
    <div v-if="readonly" class="composer-shell composer-readonly">
      <div class="readonly-copy">
        <span class="readonly-title">演示对话 · 只读</span>
        <span class="readonly-hint">可以翻看完整流程，想自己试请新建一个对话</span>
      </div>
      <button class="readonly-cta" @click="startOwn">开始我自己的对话</button>
    </div>

    <div v-else ref="plusWrap" class="composer-shell">
      <PlusMenu
        v-if="plusOpen"
        @close="plusOpen = false"
        @pick-image="pickFiles"
        @open-docs="emit('open-panel', 'docs')"
        @open-kb="emit('open-panel', 'kb')"
        @task="onTask"
      />

      <div v-if="chat.pendingImages.length || chat.pendingDocs.length" class="composer-attach">
        <div v-for="(img, i) in chat.pendingImages" :key="img.url" class="attach-thumb">
          <img :src="img.url" alt="待发送图片" />
          <button class="attach-remove" title="移除" @click="chat.removeImage(i)">
            <AppIcon name="close" :size="11" />
          </button>
        </div>

        <div
          v-for="(doc, i) in chat.pendingDocs"
          :key="doc.key"
          class="attach-file"
          :class="{ 'is-failed': doc.status === 'failed' }"
          :title="doc.status === 'failed' ? '上传失败，可移除后重试' : '点击查看资料库'"
          @click="emit('open-panel', 'docs')"
        >
          <AppIcon name="file" :size="15" class="attach-file-icon" />
          <span class="attach-file-name">{{ doc.name }}</span>
          <span class="attach-file-state">{{ docState(doc) }}</span>
          <button class="attach-file-remove" title="从输入区移除（文件仍在资料库）" @click.stop="chat.dismissDoc(i)">
            <AppIcon name="close" :size="11" />
          </button>
        </div>
      </div>

      <div class="composer-row">
        <button
          class="composer-icon plus-btn"
          :class="{ 'is-open': plusOpen, 'is-busy': uploading }"
          title="添加照片和文件"
          @click="togglePlus"
        >
          <AppIcon name="plus" :size="20" />
        </button>

        <textarea
          ref="ta"
          v-model="text"
          class="composer-input"
          rows="1"
          placeholder="询问 Job Copilot"
          @input="autosize"
          @keydown="onKeydown"
        />

        <input
          ref="fileInput"
          type="file"
          :accept="FILE_ACCEPT"
          multiple
          style="display: none"
          @change="onFilesChange"
        />

        <button v-if="chat.sending" class="composer-icon" title="停止生成" @click="chat.stopStreaming()">
          <AppIcon name="stop" :size="15" />
        </button>
        <button v-else class="send-btn" :disabled="!canSend" title="发送 (Enter)" @click="send">
          <AppIcon name="arrowUp" :size="18" />
        </button>
      </div>
    </div>
  </div>
</template>
