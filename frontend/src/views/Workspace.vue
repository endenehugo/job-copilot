<script setup>
import { onMounted, watch } from 'vue'
import { notifyError } from '../api'
import { useChatStore } from '../stores/chat'
import { useConversationStore } from '../stores/conversation'
import { useInterviewStore } from '../stores/interview'
import { useResumeStore } from '../stores/resume'
import { openStartDialog } from '../utils/startDialog'

import ConversationList from '../components/sidebar/ConversationList.vue'
import DocumentPanel from '../components/sidebar/DocumentPanel.vue'
import KnowledgePanel from '../components/sidebar/KnowledgePanel.vue'

import MessageList from '../components/chat/MessageList.vue'
import Composer from '../components/chat/Composer.vue'
import InterviewView from '../components/interview/InterviewView.vue'
import AnalysisResultView from '../components/analysis/AnalysisResultView.vue'
import RewriteResultView from '../components/rewrite/RewriteResultView.vue'
import StartWorkDialog from '../components/interview/StartWorkDialog.vue'

const conv = useConversationStore()
const chat = useChatStore()
const interview = useInterviewStore()
const resume = useResumeStore()

const CHIPS = [
  '这份文档主要介绍了什么？',
  '帮我总结刚上传的简历的亮点',
  'RAG 的检索链路一般包含哪些环节？',
  '模拟面试一般会问哪些项目深挖问题？',
]

const ACTIVE_TITLES = {
  interview: '🎤 模拟面试',
  analysis: '📊 JD 分析',
  rewrite: '✏️ 项目优化',
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
  // 旧代码没有 catch：sendStream 失败会变成未处理的 Promise rejection，界面毫无反馈
  chat.sendStream(text).catch((err) => notifyError(err))
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
        <el-collapse-item title="📚 知识库" name="kb">
          <KnowledgePanel />
        </el-collapse-item>
      </el-collapse>
    </aside>

    <main class="workspace">
      <header class="topbar">
        <span class="title">
          {{ ACTIVE_TITLES[conv.activeType] ? `${ACTIVE_TITLES[conv.activeType]} · ${conv.current?.title || ''}` : conv.current?.title || 'Job Copilot' }}
        </span>
        <span class="badge">FastAPI + Vue3 · LLM + RAG</span>
      </header>

      <div class="main-area">
        <!-- 子节点视图：面试 / JD 分析 / 项目优化 -->
        <InterviewView v-if="conv.activeType === 'interview'" />
        <AnalysisResultView v-else-if="conv.activeType === 'analysis'" />
        <RewriteResultView v-else-if="conv.activeType === 'rewrite'" />

        <!-- 聊天视图 -->
        <template v-else>
          <MessageList v-if="chat.messages.length" />

          <section v-if="!chat.messages.length" class="welcome">
            <h2>欢迎使用 Job Copilot</h2>
            <p>上传简历 → 输入 JD → 查看匹配评分 → 优化项目 → 模拟面试</p>
            <div class="chips">
              <div v-for="c in CHIPS" :key="c" class="chip" @click="sendChip(c)">{{ c }}</div>
            </div>
            <!-- 发起任务入口：三入口对称（P5 决策），对话框由 StartWorkDialog 承载 -->
            <div class="start-entry">
              <div class="start-entry-title">或直接发起一场任务</div>
              <div class="start-buttons">
                <el-button type="primary" size="small" @click="openStartDialog('interview')">🎤 模拟面试</el-button>
                <el-button type="primary" plain size="small" @click="openStartDialog('analysis')">📊 分析 JD</el-button>
                <el-button type="primary" plain size="small" @click="openStartDialog('rewrite')">✏️ 优化项目</el-button>
              </div>
            </div>
          </section>

          <Composer />
        </template>
      </div>

      <StartWorkDialog />
    </main>
  </div>
</template>

<style scoped>
.start-entry {
  margin-top: 16px;
  padding: 12px 16px;
  border: 1px dashed var(--el-color-primary-light-5, #a0cfff);
  border-radius: 8px;
  background: var(--el-color-primary-light-9, #ecf5ff);
}
.start-entry-title {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 8px;
  color: var(--el-color-primary);
}
.start-buttons {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
</style>
