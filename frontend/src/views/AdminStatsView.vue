<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { adminApi, notifyError } from '../api'
import { useAuthStore } from '../stores/auth'

// 管理员监控页：数据全部来自 /admin/stats/*（llm_usage 表聚合）。
// 独立路由 /admin，不在侧栏挂导航入口——展示站的主界面不该出现运维概念，
// 管理员从侧栏底部头像菜单进入。

const auth = useAuthStore()

const loading = ref(false)
const hours = ref(24)
const summary = ref(null)
const recent = ref([])

const loginForm = reactive({ username: '', password: '' })
const loggingIn = ref(false)

const quotaPercent = computed(() => {
  const q = summary.value?.quota
  if (!q || !q.global_daily_token_limit || q.tokens_today < 0) return null
  return Math.min(100, Math.round((q.tokens_today / q.global_daily_token_limit) * 1000) / 10)
})

const totalCards = computed(() => {
  const t = summary.value?.total
  if (!t) return []
  const tokens = (t.in_tokens || 0) + (t.out_tokens || 0)
  return [
    { label: 'LLM 调用', value: t.llm_calls, hint: '全部模型的调用次数' },
    { label: 'Token 消耗', value: fmtTokens(tokens), hint: `输入 ${fmtTokens(t.in_tokens)} / 输出 ${fmtTokens(t.out_tokens)}` },
    { label: 'Chat 请求', value: t.chat_requests, hint: '按 trace 去重的对话请求数' },
    { label: '错误', value: t.errors, hint: '调用失败（额度/网络/参数）', danger: t.errors > 0 },
    { label: '降级切换', value: t.fallbacks, hint: '主模型额度耗尽后走降级链', warn: t.fallbacks > 0 },
  ]
})

function fmtTokens(n) {
  if (n == null) return '--'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(n)
}

function fmtMs(ms) {
  if (ms == null) return '--'
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${Math.round(ms)}ms`
}

async function load() {
  loading.value = true
  try {
    const [summaryRes, recentRes] = await Promise.all([
      adminApi.statsSummary(hours.value),
      adminApi.recentUsage(100),
    ])
    summary.value = summaryRes.data
    recent.value = recentRes.data.items || []
  } catch (err) {
    notifyError(err, '监控数据加载失败')
  } finally {
    loading.value = false
  }
}

async function login() {
  if (!loginForm.username.trim() || !loginForm.password) {
    ElMessage.warning('请输入用户名和密码')
    return
  }
  loggingIn.value = true
  try {
    await auth.login(loginForm.username.trim(), loginForm.password)
    await load()
  } catch (err) {
    notifyError(err, '登录失败')
  } finally {
    loggingIn.value = false
  }
}

onMounted(() => {
  if (auth.isAdmin) load()
})
</script>

<template>
  <div class="admin-page">
    <!-- 未登录/非管理员：就地登录，不跳走 -->
    <div v-if="!auth.isAdmin" class="login-wrap">
      <div class="login-card">
        <h1>运行监控</h1>
        <p class="login-hint">此页面仅管理员可见，请先登录</p>
        <el-form label-position="top" @submit.prevent>
          <el-form-item label="用户名">
            <el-input v-model="loginForm.username" autocomplete="username" />
          </el-form-item>
          <el-form-item label="密码">
            <el-input
              v-model="loginForm.password"
              type="password"
              show-password
              autocomplete="current-password"
              @keyup.enter="login"
            />
          </el-form-item>
        </el-form>
        <el-button type="primary" class="login-btn" :loading="loggingIn" @click="login">登录</el-button>
      </div>
    </div>

    <template v-else>
      <header class="admin-header">
        <div class="header-left">
          <router-link to="/" class="back-link">← 返回工作台</router-link>
          <h1>运行监控</h1>
        </div>
        <div class="header-right">
          <el-radio-group v-model="hours" size="small" @change="load">
            <el-radio-button :value="24">24 小时</el-radio-button>
            <el-radio-button :value="168">7 天</el-radio-button>
            <el-radio-button :value="720">30 天</el-radio-button>
          </el-radio-group>
          <el-button size="small" :loading="loading" @click="load">刷新</el-button>
        </div>
      </header>

      <!-- 配额水位 -->
      <section v-if="summary?.quota" class="quota-bar card">
        <div class="quota-text">
          <span class="quota-title">今日全站 Token 预算</span>
          <span class="quota-detail">
            {{ fmtTokens(summary.quota.tokens_today) }} /
            {{ fmtTokens(summary.quota.global_daily_token_limit) }}
            <span class="quota-note">（单访客每日 {{ summary.quota.visitor_daily_chat_limit || '不限' }} 次 chat）</span>
          </span>
        </div>
        <div class="quota-track">
          <div
            class="quota-fill"
            :class="{ danger: quotaPercent >= 80 }"
            :style="{ width: `${quotaPercent ?? 0}%` }"
          ></div>
        </div>
      </section>

      <!-- 总量卡片 -->
      <section class="cards">
        <div
          v-for="card in totalCards"
          :key="card.label"
          class="card stat"
          :class="{ danger: card.danger, warn: card.warn }"
        >
          <span class="stat-value">{{ card.value ?? '--' }}</span>
          <span class="stat-label">{{ card.label }}</span>
          <span class="stat-hint">{{ card.hint }}</span>
        </div>
      </section>

      <!-- 按模型 / 按用途 -->
      <section class="grid-2">
        <div class="card">
          <h2>按模型</h2>
          <el-table :data="summary?.by_model || []" size="small" :header-cell-style="{ background: 'var(--surface-sunken)' }">
            <el-table-column prop="model" label="模型" min-width="150" />
            <el-table-column prop="calls" label="调用" width="70" align="right" />
            <el-table-column label="Token" width="110" align="right">
              <template #default="{ row }">{{ fmtTokens(row.in_tokens + row.out_tokens) }}</template>
            </el-table-column>
            <el-table-column label="错误" width="70" align="right">
              <template #default="{ row }">
                <span :class="{ 'num-danger': row.errors > 0 }">{{ row.errors }}</span>
              </template>
            </el-table-column>
          </el-table>
        </div>

        <div class="card">
          <h2>按用途（耗时分布）</h2>
          <el-table :data="summary?.by_purpose || []" size="small" :header-cell-style="{ background: 'var(--surface-sunken)' }">
            <el-table-column prop="purpose" label="用途" min-width="110" />
            <el-table-column prop="calls" label="调用" width="70" align="right" />
            <el-table-column label="P50" width="90" align="right">
              <template #default="{ row }">{{ fmtMs(row.p50_ms) }}</template>
            </el-table-column>
            <el-table-column label="P95" width="90" align="right">
              <template #default="{ row }">{{ fmtMs(row.p95_ms) }}</template>
            </el-table-column>
          </el-table>
        </div>
      </section>

      <section v-if="summary?.top_owners?.length" class="card">
        <h2>用量 Top 访客</h2>
        <el-table :data="summary.top_owners" size="small" :header-cell-style="{ background: 'var(--surface-sunken)' }">
          <el-table-column prop="owner_key" label="访客" min-width="200" />
          <el-table-column prop="calls" label="调用" width="90" align="right" />
          <el-table-column label="Token" width="120" align="right">
            <template #default="{ row }">{{ fmtTokens(row.tokens) }}</template>
          </el-table-column>
        </el-table>
      </section>

      <!-- 明细 -->
      <section class="card">
        <h2>最近调用</h2>
        <el-table :data="recent" size="small" :header-cell-style="{ background: 'var(--surface-sunken)' }">
          <el-table-column prop="time" label="时间" width="120" />
          <el-table-column prop="purpose" label="用途" width="100" />
          <el-table-column prop="owner" label="访客" width="130">
            <template #default="{ row }">
              <span class="mono">{{ row.owner }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="model" label="模型" min-width="150" />
          <el-table-column label="Token" width="110" align="right">
            <template #default="{ row }">{{ fmtTokens((row.in_tokens || 0) + (row.out_tokens || 0)) }}</template>
          </el-table-column>
          <el-table-column label="耗时" width="90" align="right">
            <template #default="{ row }">{{ fmtMs(row.duration_ms) }}</template>
          </el-table-column>
          <el-table-column label="状态" width="110">
            <template #default="{ row }">
              <el-tag v-if="row.error" type="danger" size="small" disable-transitions>{{ row.error }}</el-tag>
              <el-tag v-else-if="row.fallback" type="warning" size="small" disable-transitions>降级</el-tag>
              <el-tag v-else type="success" size="small" disable-transitions>ok</el-tag>
            </template>
          </el-table-column>
        </el-table>
      </section>
    </template>
  </div>
</template>

<style scoped>
.admin-page {
  min-height: 100vh;
  background: var(--canvas);
  color: var(--text-1);
  padding: 24px clamp(16px, 4vw, 48px) 48px;
  max-width: 1080px;
  margin: 0 auto;
  box-sizing: border-box;
}
.admin-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}
.header-left {
  display: flex;
  align-items: baseline;
  gap: 14px;
}
.back-link {
  color: var(--text-2);
  text-decoration: none;
  font-size: 13px;
}
.back-link:hover {
  color: var(--text-1);
}
.admin-header h1 {
  font-size: 20px;
  margin: 0;
}
.header-right {
  display: flex;
  align-items: center;
  gap: 10px;
}
.card {
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 14px 16px;
}
.card h2 {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-2);
  margin: 0 0 10px;
}
.quota-bar {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 14px;
}
.quota-text {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 6px;
}
.quota-title {
  font-size: 13px;
  font-weight: 600;
}
.quota-detail {
  font-size: 13px;
  color: var(--text-2);
}
.quota-note {
  color: var(--text-3);
  font-size: 12px;
}
.quota-track {
  height: 6px;
  border-radius: 3px;
  background: var(--surface-sunken);
  overflow: hidden;
}
.quota-fill {
  height: 100%;
  border-radius: 3px;
  background: var(--text-1);
  transition: width 0.4s ease;
}
.quota-fill.danger {
  background: var(--danger);
}
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px;
  margin-bottom: 14px;
}
.stat {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.stat-value {
  font-size: 22px;
  font-weight: 600;
  line-height: 1.2;
}
.stat.danger .stat-value {
  color: var(--danger);
}
.stat.warn .stat-value {
  color: var(--warn);
}
.stat-label {
  font-size: 12px;
  color: var(--text-2);
}
.stat-hint {
  font-size: 11px;
  color: var(--text-3);
}
.grid-2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  margin-bottom: 14px;
}
@media (max-width: 760px) {
  .grid-2 {
    grid-template-columns: 1fr;
  }
}
.card + section.card,
section.card + section.card,
.grid-2 + section.card {
  margin-bottom: 14px;
}
.num-danger {
  color: var(--danger);
  font-weight: 600;
}
.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  color: var(--text-2);
}
.login-wrap {
  min-height: 70vh;
  display: flex;
  align-items: center;
  justify-content: center;
}
.login-card {
  width: 320px;
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 28px;
  background: var(--surface-1);
}
.login-card h1 {
  font-size: 18px;
  margin: 0 0 4px;
}
.login-hint {
  font-size: 13px;
  color: var(--text-2);
  margin: 0 0 16px;
}
.login-btn {
  width: 100%;
}
</style>
