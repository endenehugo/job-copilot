<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useResumeGenStore } from '../../stores/resumeGen'
import { useConversationStore } from '../../stores/conversation'

// 发起自动简历生成（发起对话框的"生成简历"标签页）。
// 素材只取会话内已解析的简历；这两个输入框对应 make-resume 的"缺失字段补问"，
// 只问真正影响交付的两项，其余缺失交给正文里的【待补】标记。
const gen = useResumeGenStore()
const conv = useConversationStore()

const school = ref('')
const major = ref('')

const emit = defineEmits(['started'])

async function onGenerate() {
  if (!conv.currentId) {
    ElMessage.warning('请先新建或选择一个会话')
    return
  }
  const extra = {}
  if (school.value.trim()) extra['学校名称'] = school.value.trim()
  if (major.value.trim()) extra['专业名称'] = major.value.trim()

  try {
    const res = await gen.generate(conv.currentId, extra)
    // 必须带上 generation_id：持久化后每一版有独立记录，
    // 传空串会让主区落回"没有版本"的空态，刷新后也定位不到刚生成的那一版。
    conv.selectNode('resume', conv.currentId, res.generation_id)
    ElMessage.success(`简历已生成（第 ${res.version} 版）`)
    emit('started')
  } catch {
    // 失败提示已由 store 统一弹出，这里不重复打扰
  }
}
</script>

<template>
  <div class="start-form start-form--resume">
    <p class="gen-hint">
      基于当前会话里已上传并解析的简历，结合最近一次 JD 分析，生成一份 A4 单栏可编辑简历。
      内容只取自简历原文，缺信息的字段会以<span class="pending">【待补】</span>标注。
    </p>

    <div class="extra-grid">
      <el-input v-model="school" placeholder="学校名称（可选，原文没有时填）" />
      <el-input v-model="major" placeholder="专业名称（可选）" />
    </div>

    <div class="start-actions">
      <el-button
        type="primary"
        :loading="gen.loading"
        :disabled="!conv.currentId"
        @click="onGenerate"
      >
        {{ gen.loading ? '正在生成…' : '生成简历' }}
      </el-button>
      <span class="hint">约 15–20 秒，生成后可预览、打印为 PDF 或下载 HTML</span>
    </div>
  </div>
</template>

<style scoped>
.gen-hint {
  margin: 0 0 12px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--text-secondary, #6b7280);
}
.pending {
  color: #b45309;
  background: #fef3c7;
  border-radius: 2px;
  padding: 0 3px;
  font-weight: 600;
}
.extra-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.start-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 14px;
}
.hint {
  font-size: 12px;
  color: var(--text-secondary, #909399);
}
@media (max-width: 560px) {
  .extra-grid {
    grid-template-columns: 1fr;
  }
}
</style>
