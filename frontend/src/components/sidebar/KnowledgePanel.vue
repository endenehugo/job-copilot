<script setup>
import { onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { notifyError } from '../../api'
import { useKnowledgeStore } from '../../stores/knowledge'

const kb = useKnowledgeStore()

onMounted(() => kb.loadStatus())

async function onRebuild() {
  try {
    await kb.rebuild()
    ElMessage.success('重建完成')
  } catch (err) {
    notifyError(err)
  }
}

async function onSubmit() {
  try {
    await kb.submitContent()
    if (kb.reviewResult?.approved) {
      ElMessage.success('AI 审核通过，已入库')
    }
  } catch (err) {
    notifyError(err)
  }
}

async function onExpand() {
  try {
    await kb.selfExpand()
    ElMessage.success(
      `AI 扩充完成：新增 ${kb.expandResult?.added.length ?? 0} 条，驳回 ${kb.expandResult?.rejected.length ?? 0} 条`
    )
  } catch (err) {
    notifyError(err)
  }
}
</script>

<template>
  <div>
    <div style="display: flex; gap: 8px; align-items: center; margin-bottom: 8px">
      <el-button size="small" :loading="kb.rebuilding" @click="onRebuild">重建索引</el-button>
      <span v-if="kb.status" class="hint">
        {{ kb.status.index_exists ? `已建索引 · ${kb.status.total_documents} 条` : '未建索引' }}
      </span>
    </div>

    <div class="hint" style="margin-bottom: 6px">
      找面试题不用搜：直接在对话里问，AI 会结合本知识库与自身知识回答。
    </div>

    <el-divider style="margin: 10px 0" />

    <div class="hint" style="margin-bottom: 6px">知识库自主更新（AI 审核通过才入库）</div>
    <el-input
      v-model="kb.newContent"
      type="textarea"
      :rows="3"
      size="small"
      placeholder="粘贴面试题、八股或工程要点… AI 会判断是否为面试相关内容"
    />
    <div style="display: flex; gap: 8px; margin-top: 6px">
      <el-button size="small" type="primary" :loading="kb.submitting" @click="onSubmit">提交 AI 审核</el-button>
      <el-button size="small" :loading="kb.expanding" @click="onExpand">AI 自主扩充</el-button>
    </div>

    <div v-if="kb.reviewResult" class="kb-result">
      <div class="kb-title">
        {{ kb.reviewResult.approved ? '✅ 审核通过，已入库' : '❌ 未通过审核' }}
        <span v-if="kb.reviewResult.approved" class="kb-cat">{{ kb.reviewResult.review?.category }}</span>
      </div>
      <div class="kb-content">{{ kb.reviewResult.review?.reason }}</div>
    </div>

    <div v-if="kb.expandResult" class="kb-result">
      <div class="kb-title">
        AI 扩充：新增 {{ kb.expandResult.added.length }} 条 / 驳回 {{ kb.expandResult.rejected.length }} 条
      </div>
      <div v-for="(a, i) in kb.expandResult.added" :key="'a' + i" class="kb-content">
        ✅ {{ a.title }}（{{ a.category }}）
      </div>
      <div v-for="(r, i) in kb.expandResult.rejected" :key="'r' + i" class="kb-content">
        ❌ {{ r.title }}：{{ r.reason }}
      </div>
    </div>
  </div>
</template>
