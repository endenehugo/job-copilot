<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { notifyError } from '../../api'
import { useAuthStore } from '../../stores/auth'
import { useModelStore } from '../../stores/model'
import { useRouter } from 'vue-router'
import AppIcon from './AppIcon.vue'

const auth = useAuthStore()
const modelStore = useModelStore()
const router = useRouter()

const menuOpen = ref(false)
const dialogVisible = ref(false)
const submitting = ref(false)
const form = reactive({ username: '', password: '' })

const initial = computed(() => (auth.displayName || '访').trim().charAt(0).toUpperCase())
const roleLabel = computed(() => (auth.isAdmin ? '管理员' : '未登录'))

function onTrigger() {
  if (auth.isAdmin) menuOpen.value = !menuOpen.value
  else openDialog()
}

function openDialog() {
  form.username = ''
  form.password = ''
  dialogVisible.value = true
}

async function submit() {
  if (!form.username.trim() || !form.password) {
    ElMessage.warning('请输入用户名和密码')
    return
  }
  submitting.value = true
  try {
    const data = await auth.login(form.username.trim(), form.password)
    dialogVisible.value = false
    ElMessage.success(`欢迎，${data.username}`)
    // 角色变了：可选模型清单随之变化，重新拉取
    modelStore.load().catch(() => {})
  } catch (err) {
    notifyError(err, '登录失败')
  } finally {
    submitting.value = false
  }
}

function logout() {
  menuOpen.value = false
  auth.logout()
  ElMessage.success('已退出管理员身份')
  modelStore.load().catch(() => {})
}

function openStats() {
  menuOpen.value = false
  router.push('/admin')
}

function closeOnOutside() {
  menuOpen.value = false
}

onMounted(() => document.addEventListener('click', closeOnOutside))
onBeforeUnmount(() => document.removeEventListener('click', closeOnOutside))
</script>

<template>
  <div class="sidebar-footer" @click.stop>
    <div class="footer-menu" v-if="menuOpen">
      <button class="footer-menu-item" @click="openStats">
        <AppIcon name="chart" :size="15" /> 运行监控
      </button>
      <button class="footer-menu-item" @click="logout">
        <AppIcon name="logout" :size="15" /> 退出登录
      </button>
    </div>

    <button class="user-row" :title="auth.isAdmin ? '当前为管理员' : '点击登录管理员'" @click="onTrigger">
      <span class="user-avatar">
        <template v-if="auth.isAdmin">{{ initial }}</template>
        <AppIcon v-else name="user" :size="15" />
      </span>
      <span class="user-meta">
        <span class="user-name">{{ auth.displayName }}</span>
        <span class="user-role">{{ roleLabel }}</span>
      </span>
      <span class="user-more"><AppIcon name="more" :size="16" /></span>
    </button>

    <el-dialog v-model="dialogVisible" title="管理员登录" width="360px" append-to-body class="app-dialog">
      <el-form label-position="top" @submit.prevent>
        <el-form-item label="用户名">
          <el-input v-model="form.username" placeholder="管理员用户名" autocomplete="username" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input
            v-model="form.password"
            type="password"
            show-password
            placeholder="管理员密码"
            autocomplete="current-password"
            @keyup.enter="submit"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submit">登录</el-button>
      </template>
    </el-dialog>
  </div>
</template>
