<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { notifyError } from '../api'
import { useChatStore } from '../stores/chat'
import { useConversationStore } from '../stores/conversation'
import { useInterviewStore } from '../stores/interview'
import { useResumeStore } from '../stores/resume'
import { useResumeGenStore } from '../stores/resumeGen'
import { useAuthStore } from '../stores/auth'
import { useModelStore } from '../stores/model'

import SidebarNav from '../components/shell/SidebarNav.vue'
import SidebarFooter from '../components/shell/SidebarFooter.vue'
import AppIcon from '../components/shell/AppIcon.vue'
import ConversationList from '../components/sidebar/ConversationList.vue'
import DocumentPanel from '../components/sidebar/DocumentPanel.vue'
import KnowledgePanel from '../components/sidebar/KnowledgePanel.vue'

import MessageList from '../components/chat/MessageList.vue'
import Composer from '../components/chat/Composer.vue'
import ModelSelector from '../components/topbar/ModelSelector.vue'
import InterviewView from '../components/interview/InterviewView.vue'
import AnalysisResultView from '../components/analysis/AnalysisResultView.vue'
import RewriteResultView from '../components/rewrite/RewriteResultView.vue'
import ResumeGenerateView from '../components/resume/ResumeGenerateView.vue'
import StartWorkDialog from '../components/interview/StartWorkDialog.vue'

const conv = useConversationStore()
const chat = useChatStore()
const interview = useInterviewStore()
const resume = useResumeStore()
const resumeGen = useResumeGenStore()
const auth = useAuthStore()
const modelStore = useModelStore()

const ACTIVE_TITLES = {
  interview: '模拟面试',
  analysis: 'JD 分析',
  rewrite: '项目优化',
}

const collapsed = ref(localStorage.getItem('jc_sidebar_collapsed') === '1')
const mobileOpen = ref(false)
const narrow = ref(false)
const docsOpen = ref(false)
const kbOpen = ref(false)
let mq = null

function syncNarrow(e) {
  narrow.value = e.matches
  if (!e.matches) mobileOpen.value = false
}

function onToggleSidebar() {
  if (narrow.value) mobileOpen.value = !mobileOpen.value
  else {
    collapsed.value = !collapsed.value
    localStorage.setItem('jc_sidebar_collapsed', collapsed.value ? '1' : '0')
  }
}

function onOpenPanel(panel) {
  if (panel === 'docs') docsOpen.value = true
  else if (panel === 'kb') kbOpen.value = true
}

// 切换会话后：聊天从 detail 回放，简历版本等按会话维度刷新。
// 分析/优化/面试子节点数据由树展开时懒加载，不随会话切换拉取。
watch(
  () => conv.detail,
  (detail) => chat.loadFromDetail(detail)
)
watch(
  () => conv.currentId,
  (cid) => {
    chat.stopStreaming() // 旧会话的流式回答不再接收，避免后台空跑
    resume.load(cid)
    // 简历结果已落库，跨会话时会按 activeNodeId 重新拉取；
    // 这里只清"上一个会话的预览"，不能无条件清（会误伤刚打开的节点）。
    resumeGen.resetIfOtherConversation(cid)
    mobileOpen.value = false
  }
)

onMounted(async () => {
  mq = window.matchMedia('(max-width: 900px)')
  syncNarrow(mq)
  mq.addEventListener('change', syncNarrow)

  // 先验 token（失效则降级普通用户），再按角色拉模型清单；失败不阻塞主功能
  await auth.restore().catch(() => {})
  await modelStore.load().catch(() => {})
  try {
    await conv.loadList()
  } catch (err) {
    notifyError(err)
  }
})

onBeforeUnmount(() => {
  if (mq) mq.removeEventListener('change', syncNarrow)
})
</script>

<template>
  <div class="app-shell" :class="{ 'is-collapsed': collapsed && !narrow }">
    <aside class="sidebar" :class="{ 'is-open': mobileOpen }">
      <div class="sidebar-head">
        <span class="brand">Job Copilot</span>
        <button class="icon-btn" title="收起侧栏" @click="onToggleSidebar">
          <AppIcon name="panel" :size="17" />
        </button>
      </div>

      <SidebarNav @open-panel="onOpenPanel" />

      <div class="sidebar-scroll">
        <div class="section-label">最近</div>
        <ConversationList />
      </div>

      <SidebarFooter />
    </aside>

    <div v-if="mobileOpen" class="sidebar-backdrop" @click="mobileOpen = false"></div>

    <main class="workspace">
      <header class="main-head">
        <button v-if="collapsed || narrow" class="icon-btn" title="展开侧栏" @click="onToggleSidebar">
          <AppIcon name="panel" :size="17" />
        </button>
        <span class="main-title">
          {{
            ACTIVE_TITLES[conv.activeType]
              ? `${ACTIVE_TITLES[conv.activeType]}${conv.current?.title ? ' · ' + conv.current.title : ''}`
              : conv.current?.title || ''
          }}
        </span>
        <div class="main-head-right">
          <ModelSelector />
        </div>
      </header>

      <div class="main-area">
        <!-- 子节点视图：面试 / JD 分析 / 项目优化 / 简历生成 -->
        <InterviewView v-if="conv.activeType === 'interview'" />
        <AnalysisResultView v-else-if="conv.activeType === 'analysis'" />
        <RewriteResultView v-else-if="conv.activeType === 'rewrite'" />
        <ResumeGenerateView v-else-if="conv.activeType === 'resume'" />

        <!-- 聊天视图：空态与对话态共用同一个 Composer 实例 -->
        <div v-else class="thread" :class="{ 'thread--empty': !chat.messages.length }">
          <MessageList v-if="chat.messages.length" />
          <h1 v-else class="thread-hero">随时可以开始。</h1>
          <Composer :variant="chat.messages.length ? 'docked' : 'hero'" @open-panel="onOpenPanel" />
        </div>
      </div>

      <StartWorkDialog />

      <el-drawer v-model="docsOpen" title="资料库" size="380px" class="side-drawer">
        <DocumentPanel />
      </el-drawer>
      <el-drawer v-model="kbOpen" title="知识库" size="380px" class="side-drawer">
        <KnowledgePanel />
      </el-drawer>
    </main>
  </div>
</template>
