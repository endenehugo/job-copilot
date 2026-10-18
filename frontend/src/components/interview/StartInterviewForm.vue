<script setup>
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { jobApi, notifyError } from '../../api'
import { useInterviewStore } from '../../stores/interview'
import { useConversationStore } from '../../stores/conversation'
import { useAnalysisStore } from '../../stores/analysis'
import { startDialog } from '../../utils/startDialog'
import AppIcon from '../shell/AppIcon.vue'

// 发起面试的表单：主区空态引导与 Composer 工具条对话框共用。
// 前端先校验简历与 JD（后端 /interview/start 也会拒绝），避免点了才报错。
const interview = useInterviewStore()
const conv = useConversationStore()
const analysis = useAnalysisStore()

const emit = defineEmits(['started'])
const checking = ref(false)

// 本次面试会用的 JD：「JD 分析」页粘贴的草稿优先（哪怕还没点分析），
// 为空时后端回退到该会话最近一次分析记录里的 JD 原文。
// 把来源显式显示出来——否则用户看着输入框里有 JD、点下去却说没有，无从判断。
const draft = computed(() => analysis.jdDraftFor(conv.currentId))

// 该会话是否已有 JD 分析：后端开始面试时会取最新一条分析记录里的 JD 原文。
// 只有明确的 404 才算"没有 JD"，网络/服务异常必须抛出去，不能误判成缺 JD。
async function hasJdAnalysis(cid) {
  try {
    await jobApi.latest(cid)
    return true
  } catch (err) {
    if (err.code === 404) return false
    throw err
  }
}

async function onStart() {
  if (!conv.currentId) {
    ElMessage.warning('请先新建或选择一个会话')
    return
  }
  if (!conv.documents.length) {
    ElMessage.warning('当前会话还没有简历：请先上传简历，再发起模拟面试')
    return
  }

  if (!draft.value) {
    // 没有草稿时才去查历史分析；有草稿就直接用，不发这次多余请求
    checking.value = true
    try {
      if (!(await hasJdAnalysis(conv.currentId))) {
        // 直接把用户送到能补 JD 的标签页，而不是让他自己猜缺了什么
        startDialog.tab = 'analysis'
        ElMessage.warning('当前会话还没有 JD：粘贴岗位描述并点「开始分析」，再回来发起面试')
        return
      }
    } catch (err) {
      notifyError(err, 'JD 状态检查失败')
      return
    } finally {
      checking.value = false
    }
  }

  try {
    await interview.start(draft.value)
    // 开场只出第 1 题（后面的题目按回答自适应生成），所以不再报"共 N 题"，
    // 否则会读成"这场面试只有 1 题"
    ElMessage.success('面试已开始，题目会随回答逐轮推进')
    emit('started')
  } catch (err) {
    notifyError(err)
  }
}
</script>

<template>
  <div class="start-form">
    <el-select v-model="interview.direction" style="width: 100%">
      <el-option label="通用方向" value="general" />
      <el-option label="Python 后端" value="python_backend" />
      <el-option label="Agent / AI 应用" value="agent_ai" />
    </el-select>

    <!-- JD 来源：让"这次到底用哪份 JD"可见可改 -->
    <p class="jd-src" :class="{ 'is-empty': !draft }">
      <AppIcon name="chart" :size="13" />
      <span v-if="draft">本次使用「JD 分析」页粘贴的 JD（{{ draft.length }} 字）</span>
      <span v-else>未粘贴 JD：将复用最近一次分析（没有则需先粘贴）</span>
      <button type="button" class="jd-src-link" @click="startDialog.tab = 'analysis'">
        {{ draft ? '修改' : '去粘贴' }}
      </button>
    </p>

    <div class="start-actions">
      <el-button type="primary" :loading="interview.starting || checking" @click="onStart">
        开始面试
      </el-button>
      <span class="hint">需要会话中已有简历</span>
    </div>
  </div>
</template>

<style scoped>
.jd-src {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 10px 0 0;
  font-size: 12.5px;
  line-height: 1.5;
  color: var(--text-2);
}
.jd-src.is-empty {
  color: var(--text-3);
}
.jd-src-link {
  padding: 0;
  border: 0;
  background: none;
  font: inherit;
  color: var(--text-1);
  text-decoration: underline;
  text-underline-offset: 2px;
  cursor: pointer;
}
.jd-src-link:hover {
  color: var(--text-2);
}
.start-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 14px;
}
</style>
