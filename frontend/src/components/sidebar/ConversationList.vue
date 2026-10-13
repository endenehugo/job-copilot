<script setup>
import { onBeforeUnmount, onMounted, reactive } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { conversationApi, notifyError } from '../../api'
import { useConversationStore } from '../../stores/conversation'
import { useInterviewStore } from '../../stores/interview'
import { useAnalysisStore } from '../../stores/analysis'
import { useRewriteStore } from '../../stores/rewrite'

const store = useConversationStore()
const interview = useInterviewStore()
const analysis = useAnalysisStore()
const rewrite = useRewriteStore()
const menu = reactive({ visible: false, x: 0, y: 0, cid: '' })

// 分组展开状态持久化：{cid: {iv, an, rw}}
const EXPAND_KEY = 'jc_tree_groups'
const expanded = reactive(JSON.parse(localStorage.getItem(EXPAND_KEY) || '{}'))

function persistExpanded() {
  localStorage.setItem(EXPAND_KEY, JSON.stringify(expanded))
}

const DIRECTION_LABEL = {
  general: '通用方向',
  python_backend: 'Python 后端',
  agent_ai: 'Agent / AI 应用',
}

// 三类子节点的统一描述：分组标题、懒加载、子项渲染
const GROUPS = [
  { key: 'iv', icon: '🎯', label: '面试' },
  { key: 'an', icon: '📊', label: 'JD 分析' },
  { key: 'rw', icon: '✏️', label: '项目优化' },
]

function groupState(cid) {
  if (!expanded[cid] || typeof expanded[cid] !== 'object') {
    expanded[cid] = expanded[cid] === true ? { iv: true, an: false, rw: false } : { iv: false, an: false, rw: false }
  }
  return expanded[cid]
}

function groupCount(c, key) {
  if (key === 'iv') return c.interview_count || 0
  if (key === 'an') return c.analysis_count || 0
  return c.rewrite_count || 0
}

function onToggleGroup(c, key) {
  const state = groupState(c.conversation_id)
  state[key] = !state[key]
  persistExpanded()
  // 展开时懒加载该组数据
  if (!state[key]) return
  const cid = c.conversation_id
  if (key === 'iv') interview.loadSessions(cid)
  else if (key === 'an') analysis.loadList(cid)
  else rewrite.loadList(cid)
}

async function onCreate() {
  try {
    await store.create()
  } catch (err) {
    notifyError(err)
  }
}

async function onSelect(c) {
  if (c.conversation_id === store.currentId && store.activeType === 'conversation') return
  try {
    await store.select(c.conversation_id)
  } catch (err) {
    notifyError(err)
  }
}

async function onOpenInterview(c, node) {
  if (store.activeType === 'interview' && store.activeNodeId === node.session_id) return
  try {
    await interview.openNode(c.conversation_id, node)
  } catch (err) {
    notifyError(err)
  }
}

async function onOpenAnalysis(c, node) {
  if (store.activeType === 'analysis' && store.activeNodeId === node.id) return
  try {
    await analysis.openNode(c.conversation_id, node)
  } catch (err) {
    notifyError(err)
  }
}

async function onOpenRewrite(c, node) {
  if (store.activeType === 'rewrite' && store.activeNodeId === node.rewrite_id) return
  try {
    await rewrite.openNode(c.conversation_id, node)
  } catch (err) {
    notifyError(err)
  }
}

async function confirmDelete(title, message) {
  try {
    await ElMessageBox.confirm(message, title, {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
    return true
  } catch {
    return false // 用户点了取消，不是错误
  }
}

async function onDeleteInterview(c, node) {
  if (!(await confirmDelete('删除面试记录', '删除后该场面试的问答记录与评分将一并清除，且不可恢复。'))) return
  try {
    await interview.removeSession(c.conversation_id, node.session_id)
    ElMessage.success('面试记录已删除')
  } catch (err) {
    notifyError(err)
  }
}

async function onDeleteAnalysis(c, node) {
  const ok = await confirmDelete(
    '删除分析记录',
    '删除后该条 JD 分析将清除，且不可恢复。注意：若它是该会话最新的一条分析，模拟面试的追问将拿不到 JD。'
  )
  if (!ok) return
  try {
    await analysis.remove(c.conversation_id, node.id)
    ElMessage.success('分析记录已删除')
  } catch (err) {
    notifyError(err)
  }
}

async function onDeleteRewrite(c, node) {
  if (!(await confirmDelete('删除优化记录', '删除后该条项目优化记录将清除，且不可恢复。'))) return
  try {
    await rewrite.remove(c.conversation_id, node.rewrite_id)
    ElMessage.success('优化记录已删除')
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
  if (!(await confirmDelete('删除对话', '删除后该会话的聊天记录、上传文档与全部分析数据将一并清除，且不可恢复。'))) return
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

function fmtShort(ts) {
  return (ts || '').replace('T', ' ').slice(5, 16)
}
</script>

<template>
  <div>
    <div style="display: flex; justify-content: flex-end; margin-bottom: 8px">
      <el-button type="primary" size="small" @click="onCreate">新建对话</el-button>
    </div>
    <div v-if="!store.conversations.length" class="hint">暂无会话，点击右上角新建。右键对话可删除。</div>

    <div v-for="c in store.conversations" :key="c.conversation_id">
      <!-- 父节点：会话 -->
      <div
        class="conversation-item"
        :class="{ active: store.activeType === 'conversation' && c.conversation_id === store.currentId }"
        @click="onSelect(c)"
        @contextmenu.prevent="onContextMenu($event, c.conversation_id)"
      >
        <div class="row">
          <span
            class="caret"
            :class="{ open: Object.values(groupState(c.conversation_id)).some(Boolean) }"
            @click.stop="onToggleGroup(c, 'iv')"
          >▶</span>
          <div class="body">
            <div class="title">
              {{ c.title }}
              <span class="badges">
                <template v-for="g in GROUPS" :key="g.key">
                  <span v-if="groupCount(c, g.key)" class="mini-badge" :title="g.label">
                    {{ g.icon }}{{ groupCount(c, g.key) }}
                  </span>
                </template>
              </span>
            </div>
            <div class="preview">{{ c.last_message_preview || fmt(c.updated_at) }}</div>
          </div>
        </div>
      </div>

      <!-- 分组子节点 -->
      <div v-for="g in GROUPS" :key="g.key">
        <template v-if="groupState(c.conversation_id)[g.key]">
          <!-- 🎯 面试 -->
          <template v-if="g.key === 'iv'">
            <div class="group-title">{{ g.icon }} 面试 ({{ interview.sessionsOf(c.conversation_id).length }})</div>
            <div v-if="interview.loadingSessions[c.conversation_id]" class="hint iv-children">加载中…</div>
            <template v-else>
              <div
                v-for="s in interview.sessionsOf(c.conversation_id)"
                :key="s.session_id"
                class="interview-item"
                :class="{ active: store.activeType === 'interview' && store.activeNodeId === s.session_id }"
                @click="onOpenInterview(c, s)"
              >
                <span class="iv-title">🎯 {{ DIRECTION_LABEL[s.direction] || s.direction }} · {{ s.round_count }}轮</span>
                <span class="iv-meta">
                  <span class="iv-time">{{ fmtShort(s.updated_at) }}</span>
                  <el-tag size="small" :type="s.status === 'completed' ? 'success' : 'warning'">
                    {{ s.status === 'completed' ? (s.total_score == null ? '已结束' : `${s.total_score}分`) : '进行中' }}
                  </el-tag>
                  <el-button class="iv-del" size="small" text title="删除该场面试" @click.stop="onDeleteInterview(c, s)">🗑</el-button>
                </span>
              </div>
              <div v-if="!interview.sessionsOf(c.conversation_id).length" class="hint iv-children">暂无记录</div>
            </template>
          </template>

          <!-- 📊 JD 分析 -->
          <template v-else-if="g.key === 'an'">
            <div class="group-title">{{ g.icon }} JD 分析 ({{ analysis.listOf(c.conversation_id).length }})</div>
            <div v-if="analysis.loadingList[c.conversation_id]" class="hint iv-children">加载中…</div>
            <template v-else>
              <div
                v-for="a in analysis.listOf(c.conversation_id)"
                :key="a.id"
                class="interview-item"
                :class="{ active: store.activeType === 'analysis' && store.activeNodeId === a.id }"
                @click="onOpenAnalysis(c, a)"
              >
                <span class="iv-title">📊 {{ a.job_role || '岗位未识别' }}</span>
                <span class="iv-meta">
                  <span class="iv-time">{{ fmtShort(a.created_at) }}</span>
                  <el-tag size="small" :type="a.total_score != null ? 'success' : 'info'">
                    {{ a.total_score == null ? '未评分' : `${a.total_score}分` }}
                  </el-tag>
                  <el-button class="iv-del" size="small" text title="删除该条分析" @click.stop="onDeleteAnalysis(c, a)">🗑</el-button>
                </span>
              </div>
              <div v-if="!analysis.listOf(c.conversation_id).length" class="hint iv-children">暂无记录</div>
            </template>
          </template>

          <!-- ✏️ 项目优化 -->
          <template v-else>
            <div class="group-title">{{ g.icon }} 项目优化 ({{ rewrite.listOf(c.conversation_id).length }})</div>
            <div v-if="rewrite.loadingList[c.conversation_id]" class="hint iv-children">加载中…</div>
            <template v-else>
              <div
                v-for="r in rewrite.listOf(c.conversation_id)"
                :key="r.rewrite_id"
                class="interview-item"
                :class="{ active: store.activeType === 'rewrite' && store.activeNodeId === r.rewrite_id }"
                @click="onOpenRewrite(c, r)"
              >
                <span class="iv-title">✏️ {{ r.original_preview || '（无描述）' }}</span>
                <span class="iv-meta">
                  <span class="iv-time">{{ fmtShort(r.created_at) }}</span>
                  <el-button class="iv-del" size="small" text title="删除该条优化" @click.stop="onDeleteRewrite(c, r)">🗑</el-button>
                </span>
              </div>
              <div v-if="!rewrite.listOf(c.conversation_id).length" class="hint iv-children">暂无记录</div>
            </template>
          </template>
        </template>
      </div>
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

<style scoped>
.row {
  display: flex;
  align-items: flex-start;
  gap: 4px;
}
.caret {
  flex: none;
  font-size: 9px;
  color: var(--text-secondary, #909399);
  cursor: pointer;
  padding: 3px 2px 0 0;
  transition: transform 0.15s;
  user-select: none;
}
.caret.open {
  transform: rotate(90deg);
}
.body {
  flex: 1;
  min-width: 0;
}
.badges {
  display: inline-flex;
  gap: 4px;
  margin-left: 4px;
}
.mini-badge {
  font-size: 10px;
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9, #ecf5ff);
  border-radius: 3px;
  padding: 0 4px;
  white-space: nowrap;
}
.group-title {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary, #909399);
  padding: 4px 0 2px 24px;
}
.interview-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 6px;
  padding: 5px 8px 5px 22px;
  margin: 2px 0 2px 10px;
  border-left: 2px solid var(--border, #e5e6eb);
  border-radius: 4px;
  cursor: pointer;
  font-size: 12px;
}
.interview-item:hover {
  background: var(--el-fill-color-light, #f7f8fa);
}
.interview-item.active {
  background: var(--el-color-primary-light-9, #ecf5ff);
}
.iv-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.iv-meta {
  display: flex;
  align-items: center;
  gap: 4px;
  flex: none;
}
.iv-time {
  color: var(--text-secondary, #909399);
  font-size: 11px;
}
.iv-del {
  padding: 2px;
}
.iv-children {
  padding-left: 28px;
  margin: 2px 0;
}
</style>
