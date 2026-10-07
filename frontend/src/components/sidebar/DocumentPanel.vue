<script setup>
import { ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete } from '@element-plus/icons-vue'
import { documentApi, notifyError } from '../../api'
import { useConversationStore } from '../../stores/conversation'
import { useResumeStore } from '../../stores/resume'

const conv = useConversationStore()
const resume = useResumeStore()
const fileInput = ref(null)
const uploading = ref(false)

function pick() {
  fileInput.value.click()
}

async function onFileChange(e) {
  const file = e.target.files[0]
  e.target.value = ''
  if (!file) return
  uploading.value = true
  try {
    const form = new FormData()
    form.append('conversation_id', conv.currentId)
    form.append('file', file)
    await documentApi.upload(form)
    ElMessage.success('上传成功，已建立索引')
    await conv.refreshDetail()
    await resume.load(conv.currentId)
  } catch (err) {
    notifyError(err)
  } finally {
    uploading.value = false
  }
}

async function onRemove(doc) {
  try {
    await ElMessageBox.confirm(`确定删除文档「${doc.original_name}」？`, '提示', { type: 'warning' })
  } catch {
    return
  }
  try {
    await documentApi.remove(doc.document_id)
    ElMessage.success('已删除')
    await conv.refreshDetail()
    await resume.load(conv.currentId)
  } catch (err) {
    notifyError(err)
  }
}

async function onVersionChange(versionId) {
  try {
    await resume.selectVersion(versionId)
  } catch (err) {
    notifyError(err)
  }
}
</script>

<template>
  <div>
    <input ref="fileInput" type="file" accept=".txt,.pdf,.docx" style="display: none" @change="onFileChange" />
    <div style="display: flex; gap: 8px; margin-bottom: 8px; align-items: center">
      <el-button size="small" :loading="uploading" @click="pick">上传简历</el-button>
      <span class="hint">支持 txt / pdf / docx</span>
    </div>

    <div v-if="!conv.documents.length" class="hint">尚未上传文档。上传后自动解析并建立会话级索引。</div>
    <div v-for="d in conv.documents" :key="d.document_id" class="doc-item">
      <span class="name" :title="d.original_name">{{ d.original_name }}</span>
      <el-tag size="small" :type="d.status === 'indexed' ? 'success' : d.status === 'failed' ? 'danger' : 'info'">
        {{ d.status }}
      </el-tag>
      <el-icon style="cursor: pointer; color: #909399" @click="onRemove(d)"><Delete /></el-icon>
    </div>

    <template v-if="resume.versions.length">
      <div class="hint" style="margin: 10px 0 4px">简历版本（{{ resume.versions.length }}）</div>
      <el-select
        v-model="resume.selectedVersionId"
        size="small"
        placeholder="选择版本查看评分"
        clearable
        @change="onVersionChange"
      >
        <el-option
          v-for="v in resume.versions"
          :key="v.version_id"
          :value="v.version_id"
          :label="`v${v.version_number} · ${v.original_name}`"
        />
      </el-select>
      <div v-if="resume.versionDetail && resume.versionDetail.total_score !== null" class="doc-item" style="margin-top: 8px">
        <span class="name">总分 {{ resume.versionDetail.total_score }}</span>
        <span class="hint">
          技能 {{ resume.versionDetail.dimensions?.skill_match }} /
          项目 {{ resume.versionDetail.dimensions?.project_relevance }} /
          表达 {{ resume.versionDetail.dimensions?.expression_quality }} /
          适配 {{ resume.versionDetail.dimensions?.job_fitness }}
        </span>
      </div>
    </template>
  </div>
</template>
