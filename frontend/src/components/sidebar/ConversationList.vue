<script setup>
import { onBeforeUnmount, onMounted, reactive } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { notifyError } from '../../api'
import { useConversationStore } from '../../stores/conversation'
import { useInterviewStore } from '../../stores/interview'
import { useAnalysisStore } from '../../stores/analysis'
import { useRewriteStore } from '../../stores/rewrite'
import AppIcon from '../shell/AppIcon.vue'

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
  { key: 'iv', icon: 'target', label: '面试' },
  { key: 'an', icon: 'chart', label: 'JD 分析' },
  { key: 'rw', icon: 'pen', label: '项目优化' },
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

function totalCount(c) {
  return GROUPS.reduce((sum, g) => sum + groupCount(c, g.key), 0)
}

function isExpanded(c) {
  const st = groupState(c.conversation_id)
  return GROUPS.some((g) => st[g.key])
}

function loadGroup(cid, key) {
  if (key === 'iv') interview.loadSessions(cid)
  else if (key === 'an') analysis.loadList(cid)
  else rewrite.loadList(cid)
}

function onToggleGroup(c, key) {
  const state = groupState(c.conversation_id)
  state[key] = !state[key]
  persistExpanded()
  if (state[key]) loadGroup(c.conversation_id, key)
}

// 行首箭头的语义改为「展开/收起这个会话的全部记录」，
// 旧实现无论点哪里都只切面试分组，与箭头暗示不符。
function onToggleAll(c) {
  const state = groupState(c.conversation_id)
  if (isExpanded(c)) {
    GROUPS.forEach((g) => (state[g.key] = false))
    persistExpanded()
    return
  }
  GROUPS.forEach((g) => {
    if (!groupCount(c, g.key)) return
    state[g.key] = true
    loadGroup(c.conversation_id, g.key)
  })
  persistExpanded()
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

// 悬停出现"⋯"按钮：用按钮位置定位菜单，替代原来"只能右键"的隐藏交互
function onMoreClick(e, cid) {
  const rect = e.currentTarget.getBoundingClientRect()
  menu.visible = true
  menu.x = Math.max(8, rect.right - 132)
  menu.y = rect.bottom + 4
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

function fmtShort(ts) {
  return (ts || '').replace('T', ' ').slice(5, 16)
}
</script>

<template>
  <div class="conv-list">
    <div v-if="!store.conversations.length" class="conv-empty">还没有对话</div>

    <div v-for="c in store.conversations" :key="c.conversation_id" class="conv-node">
      <div
        class="conv-row"
        :class="{ active: store.activeType === 'conversation' && c.conversation_id === store.currentId }"
        @click="onSelect(c)"
        @contextmenu.prevent="onContextMenu($event, c.conversation_id)"
      >
        <button
          v-if="totalCount(c)"
          class="conv-caret"
          :class="{ open: isExpanded(c) }"
          :title="isExpanded(c) ? '收起记录' : '展开记录'"
          @click.stop="onToggleAll(c)"
        >
          <AppIcon name="chevronRight" :size="12" />
        </button>
        <span v-else class="conv-caret is-blank"></span>

        <span class="conv-title" :title="c.title">{{ c.title }}</span>

        <span v-if="totalCount(c) && !isExpanded(c)" class="conv-badge">{{ totalCount(c) }}</span>

        <button class="conv-more" title="更多操作" @click.stop="onMoreClick($event, c.conversation_id)">
          <AppIcon name="more" :size="15" />
        </button>
      </div>

      <!-- 子记录：保留会话→分组→记录的三级结构，只把视觉压平 -->
      <template v-if="isExpanded(c)">
        <div v-for="g in GROUPS" :key="g.key" class="conv-group">
          <template v-if="groupCount(c, g.key) || groupState(c.conversation_id)[g.key]">
            <button class="conv-group-head" @click="onToggleGroup(c, g.key)">
              <AppIcon :name="g.icon" :size="13" />
              <span>{{ g.label }}</span>
              <span class="conv-group-count">{{ groupCount(c, g.key) }}</span>
              <AppIcon
                name="chevronDown"
                :size="12"
                class="conv-group-caret"
                :class="{ open: groupState(c.conversation_id)[g.key] }"
              />
            </button>

            <template v-if="groupState(c.conversation_id)[g.key]">
              <!-- 面试 -->
              <template v-if="g.key === 'iv'">
                <div v-if="interview.loadingSessions[c.conversation_id]" class="conv-hint">加载中…</div>
                <template v-else>
                  <div
                    v-for="s in interview.sessionsOf(c.conversation_id)"
                    :key="s.session_id"
                    class="conv-child"
                    :class="{ active: store.activeType === 'interview' && store.activeNodeId === s.session_id }"
                    @click="onOpenInterview(c, s)"
                  >
                    <span class="conv-child-title">{{ DIRECTION_LABEL[s.direction] || s.direction }} · {{ s.round_count }} 轮</span>
                    <span class="conv-child-meta">
                      <span v-if="s.status === 'completed'" class="conv-score">
                        {{ s.total_score == null ? '已结束' : `${s.total_score} 分` }}
                      </span>
                      <span v-else class="conv-live">进行中</span>
                      <span class="conv-time">{{ fmtShort(s.updated_at) }}</span>
                      <button class="conv-del" title="删除该场面试" @click.stop="onDeleteInterview(c, s)">
                        <AppIcon name="trash" :size="13" />
                      </button>
                    </span>
                  </div>
                  <div v-if="!interview.sessionsOf(c.conversation_id).length" class="conv-hint">暂无记录</div>
                </template>
              </template>

              <!-- JD 分析 -->
              <template v-else-if="g.key === 'an'">
                <div v-if="analysis.loadingList[c.conversation_id]" class="conv-hint">加载中…</div>
                <template v-else>
                  <div
                    v-for="a in analysis.listOf(c.conversation_id)"
                    :key="a.id"
                    class="conv-child"
                    :class="{ active: store.activeType === 'analysis' && store.activeNodeId === a.id }"
                    @click="onOpenAnalysis(c, a)"
                  >
                    <span class="conv-child-title">{{ a.job_role || '岗位未识别' }}</span>
                    <span class="conv-child-meta">
                      <span class="conv-score">{{ a.total_score == null ? '未评分' : `${a.total_score} 分` }}</span>
                      <span class="conv-time">{{ fmtShort(a.created_at) }}</span>
                      <button class="conv-del" title="删除该条分析" @click.stop="onDeleteAnalysis(c, a)">
                        <AppIcon name="trash" :size="13" />
                      </button>
                    </span>
                  </div>
                  <div v-if="!analysis.listOf(c.conversation_id).length" class="conv-hint">暂无记录</div>
                </template>
              </template>

              <!-- 项目优化 -->
              <template v-else>
                <div v-if="rewrite.loadingList[c.conversation_id]" class="conv-hint">加载中…</div>
                <template v-else>
                  <div
                    v-for="r in rewrite.listOf(c.conversation_id)"
                    :key="r.rewrite_id"
                    class="conv-child"
                    :class="{ active: store.activeType === 'rewrite' && store.activeNodeId === r.rewrite_id }"
                    @click="onOpenRewrite(c, r)"
                  >
                    <span class="conv-child-title">{{ r.original_preview || '（无描述）' }}</span>
                    <span class="conv-child-meta">
                      <span class="conv-time">{{ fmtShort(r.created_at) }}</span>
                      <button class="conv-del" title="删除该条优化" @click.stop="onDeleteRewrite(c, r)">
                        <AppIcon name="trash" :size="13" />
                      </button>
                    </span>
                  </div>
                  <div v-if="!rewrite.listOf(c.conversation_id).length" class="conv-hint">暂无记录</div>
                </template>
              </template>
            </template>
          </template>
        </div>
      </template>
    </div>

    <div
      v-if="menu.visible"
      class="context-menu"
      :style="{ left: menu.x + 'px', top: menu.y + 'px' }"
      @click.stop
      @contextmenu.prevent
    >
      <div class="context-menu-item danger" @click="onDelete">
        <AppIcon name="trash" :size="14" />
        删除对话
      </div>
    </div>
  </div>
</template>
