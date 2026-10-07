<script setup>
import { computed, onMounted, watch } from 'vue'
import { notifyError } from '../api'
import { useAnalysisStore } from '../stores/analysis'
import { useChatStore } from '../stores/chat'
import { useConversationStore } from '../stores/conversation'
import { useInterviewStore } from '../stores/interview'
import { useResumeStore } from '../stores/resume'
import { useUiStore } from '../stores/ui'

import ConversationList from '../components/sidebar/ConversationList.vue'
import DocumentPanel from '../components/sidebar/DocumentPanel.vue'
import JdPanel from '../components/sidebar/JdPanel.vue'
import RewritePanel from '../components/sidebar/RewritePanel.vue'
import InterviewSidebar from '../components/sidebar/InterviewSidebar.vue'
import KnowledgePanel from '../components/sidebar/KnowledgePanel.vue'

import MessageList from '../components/chat/MessageList.vue'
import Composer from '../components/chat/Composer.vue'
import ScoreCard from '../components/panels/ScoreCard.vue'
import ProjectRewriteCard from '../components/panels/ProjectRewriteCard.vue'
import InterviewSession from '../components/panels/InterviewSession.vue'

const conv = useConversationStore()
const chat = useChatStore()
const analysis = useAnalysisStore()
const interview = useInterviewStore()
const resume = useResumeStore()
const ui = useUiStore()

const CHIPS = [
  '这份文档主要介绍了什么？',
  '帮我总结刚上传的简历的亮点',
  'RAG 的检索链路一般包含哪些环节？',
  '模拟面试一般会问哪些项目深挖问题？',
]

const hasPanels = computed(
  () => !!(analysis.analysis || analysis.rewrite || interview.session || interview.transcript)
)

// 切换会话后：聊天从 detail 回放，其余各模块按会话维度刷新（不自动弹抽屉）
watch(
  () => conv.detail,
  (detail) => chat.loadFromDetail(detail)
)
watch(
  () => conv.currentId,
  (cid) => {
    analysis.loadLatest(cid)
    interview.loadHistory(cid)
    resume.load(cid)
  }
)

onMounted(async () => {
  try {
    await conv.loadList()
  } catch (err) {
    notifyError(err)
  }
})

function sendChip(text) {
  chat.sendStream(text)
}
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <h1>Job Copilot</h1>
        <div class="subtitle">求职助手 Agent 平台</div>
      </div>

      <el-collapse>
        <el-collapse-item title="💬 会话" name="conv">
          <ConversationList />
        </el-collapse-item>
        <el-collapse-item title="📄 文档" name="doc">
          <DocumentPanel />
        </el-collapse-item>
        <el-collapse-item title="🎯 JD 分析" name="jd">
          <JdPanel />
        </el-collapse-item>
        <el-collapse-item title="✏️ 项目优化" name="rewrite">
          <RewritePanel />
        </el-collapse-item>
        <el-collapse-item title="🎤 模拟面试" name="interview">
          <InterviewSidebar />
        </el-collapse-item>
        <el-collapse-item title="📚 知识库" name="kb">
          <KnowledgePanel />
        </el-collapse-item>
      </el-collapse>
    </aside>

    <main class="workspace">
      <header class="topbar">
        <span class="title">{{ conv.current?.title || 'Job Copilot' }}</span>
        <div style="display: flex; gap: 8px; align-items: center">
          <el-button size="small" :type="ui.analysisDrawerOpen ? 'primary' : 'default'" @click="ui.toggleDrawer()">
            📊 分析面板
          </el-button>
          <span class="badge">FastAPI + Vue3 · LLM + RAG</span>
        </div>
      </header>

      <div class="main-area">
        <MessageList v-if="chat.messages.length" />

        <section v-if="!chat.messages.length" class="welcome">
          <h2>欢迎使用 Job Copilot</h2>
          <p>上传简历 → 输入 JD → 查看匹配评分 → 优化项目 → 模拟面试</p>
          <div class="chips">
            <div v-for="c in CHIPS" :key="c" class="chip" @click="sendChip(c)">{{ c }}</div>
          </div>
        </section>

        <Composer />
      </div>
    </main>

    <!-- 分析结果统一在右侧抽屉展示，聊天区保持纯净流 -->
    <el-drawer v-model="ui.analysisDrawerOpen" title="📊 分析面板" direction="rtl" size="520px">
      <div class="drawer-panels">
        <ScoreCard v-if="analysis.analysis" />
        <ProjectRewriteCard v-if="analysis.rewrite" />
        <InterviewSession v-if="interview.session || interview.transcript" />
        <div
          v-if="!hasPanels"
          class="hint"
          style="text-align: center; padding: 40px 0"
        >
          暂无分析结果。可通过左侧「JD 分析 / 项目优化 / 模拟面试」发起。
        </div>
      </div>
    </el-drawer>
  </div>
</template>
