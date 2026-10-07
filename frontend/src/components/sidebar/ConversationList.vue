<script setup>
import { onBeforeUnmount, onMounted, reactive } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { conversationApi, notifyError } from '../../api'
import { useConversationStore } from '../../stores/conversation'

const store = useConversationStore()
const menu = reactive({ visible: false, x: 0, y: 0, cid: '' })

async function onCreate() {
  try {
    await store.create()
  } catch (err) {
    notifyError(err)
  }
}

async function onSelect(id) {
  if (id === store.currentId) return
  try {
    await store.select(id)
  } catch (err) {
    notifyError(err)
  }
}

function onContextMenu(e, cid) {
  menu.visible = true
  menu.x = e.clientX
  menu.y = e.clientY
  menu.cid = cid
}

function closeMenu() {
  menu.visible = false
}

async function onDelete() {
  const cid = menu.cid
  closeMenu()
  try {
    await ElMessageBox.confirm(
      '删除后该会话的聊天记录、上传文档与全部分析数据将一并清除，且不可恢复。',
      '删除对话',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  try {
    await store.remove(cid)
    ElMessage.success('对话已删除')
  } catch (err) {
    notifyError(err)
  }
}

onMounted(() => document.addEventListener('click', closeMenu))
onBeforeUnmount(() => document.removeEventListener('click', closeMenu))

function fmt(ts) {
  return (ts || '').replace('T', ' ').slice(0, 16)
}
</script>

<template>
  <div>
    <div style="display: flex; justify-content: flex-end; margin-bottom: 8px">
      <el-button type="primary" size="small" @click="onCreate">新建对话</el-button>
    </div>
    <div v-if="!store.conversations.length" class="hint">暂无会话，点击右上角新建。右键对话可删除。</div>
    <div
      v-for="c in store.conversations"
      :key="c.conversation_id"
      class="conversation-item"
      :class="{ active: c.conversation_id === store.currentId }"
      @click="onSelect(c.conversation_id)"
      @contextmenu.prevent="onContextMenu($event, c.conversation_id)"
    >
      <div class="title">{{ c.title }}</div>
      <div class="preview">{{ c.last_message_preview || fmt(c.updated_at) }}</div>
    </div>

    <div
      v-if="menu.visible"
      class="context-menu"
      :style="{ left: menu.x + 'px', top: menu.y + 'px' }"
      @click.stop
      @contextmenu.prevent
    >
      <div class="context-menu-item danger" @click="onDelete">🗑 删除对话</div>
    </div>
  </div>
</template>
