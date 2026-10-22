import client, { longClient, BULK_TIMEOUT } from './client'

export { notifyError, ADMIN_TOKEN_KEY } from './client'

// 分组依据：普通 CRUD 走 30 秒；上传 / LLM 分析 / 导出走 120 秒；
// 知识库重建与自主扩充实测 1~2 分钟，单独给 300 秒。

export const authApi = {
  login: (data) => client.post('/auth/login', data),
  me: () => client.get('/auth/me'),
}

export const modelsApi = {
  list: () => client.get('/models'),
}

// 内置技能清单（标题/分组/一句话说明），输入框的技能面板用
export const skillsApi = {
  list: () => client.get('/skills'),
}

export const conversationApi = {
  create: (data) => client.post('/conversation/create', data),
  list: (limit = 50) => client.get('/conversation/list', { params: { limit } }),
  detail: (id) => client.get('/conversation/detail', { params: { conversation_id: id } }),
  chat: (data) => longClient.post('/conversation/chat', data),
  chatStream: (data) => client.post('/conversation/chat/stream', data),
  uploadImage: (formData) => longClient.post('/conversation/image/upload', formData),
  remove: (conversationId) => client.post('/conversation/delete', { conversation_id: conversationId }),
  pin: (conversationId, pinned) =>
    client.post('/conversation/pin', { conversation_id: conversationId, pinned }),
}

export const documentApi = {
  // 上传要解析文档并重建向量索引，文档越多越慢
  upload: (formData) => longClient.post('/document/upload', formData),
  remove: (documentId) => client.post('/document/delete', { document_id: documentId }),
}

export const jobApi = {
  analyze: (data) => longClient.post('/job/analyze', data),
  analyzeScreenshot: (data) => longClient.post('/job/analyze-from-screenshot', data),
  latest: (conversationId) =>
    client.get('/job/analysis/latest', { params: { conversation_id: conversationId } }),
  list: (conversationId) =>
    client.get('/job/analysis/list', { params: { conversation_id: conversationId } }),
  detail: (analysisId) => client.get('/job/analysis/detail', { params: { analysis_id: analysisId } }),
  remove: (analysisId) => client.post('/job/analysis/delete', { analysis_id: analysisId }),
}

export const resumeApi = {
  // 自动生成简历：一次 LLM 抽取 + 模板渲染，实测 15–20s，走长超时客户端
  generate: (data) => longClient.post('/resume/generate', data),
  // 生成结果的版本线（新表 resume_generations，与上传简历打分的 versions 是两回事）
  generations: (conversationId) =>
    client.get('/resume/generations', { params: { conversation_id: conversationId } }),
  generationDetail: (generationId) => client.get(`/resume/generations/${generationId}`),
  // 补字段重渲染：0 次 LLM，约 20ms，走普通客户端
  regenerate: (generationId, data) =>
    client.post(`/resume/generations/${generationId}/regenerate`, data),
  rewrite: (data) => longClient.post('/resume/project/rewrite', data),
  rewrites: (conversationId) =>
    client.get('/resume/project/rewrites', { params: { conversation_id: conversationId } }),
  rewriteDetail: (rewriteId) =>
    client.get('/resume/project/rewrite/detail', { params: { rewrite_id: rewriteId } }),
  removeRewrite: (rewriteId) => client.post('/resume/project/rewrite/delete', { rewrite_id: rewriteId }),
  versions: (conversationId) =>
    client.get('/resume/versions/list', { params: { conversation_id: conversationId } }),
  versionDetail: (versionId) =>
    client.get('/resume/versions/detail', { params: { version_id: versionId } }),
  compare: (conversationId) =>
    client.get('/resume/versions/compare', { params: { conversation_id: conversationId } }),
}

export const interviewApi = {
  start: (data) => longClient.post('/interview/start', data),
  answer: (data) => longClient.post('/interview/answer', data),
  list: (conversationId) => client.get('/interview/list', { params: { conversation_id: conversationId } }),
  detail: (sessionId) => client.get('/interview/detail', { params: { session_id: sessionId } }),
  remove: (sessionId) => client.post('/interview/delete', { session_id: sessionId }),
}

export const knowledgeApi = {
  rebuild: () => client.post('/knowledge/rebuild', null, { timeout: BULK_TIMEOUT }),
  query: (query, k = 4) => client.get('/knowledge/query', { params: { query, k } }),
  categories: () => client.get('/knowledge/categories'),
  status: () => client.get('/knowledge/status'),
  submit: (content) => client.post('/knowledge/entries', { content }),
  listEntries: (status = 'approved', limit = 50) =>
    client.get('/knowledge/entries', { params: { status, limit } }),
  removeEntry: (entryId) => client.delete(`/knowledge/entries/${entryId}`),
  selfExpand: (count = 3) => client.post('/knowledge/self-expand', { count }, { timeout: BULK_TIMEOUT }),
}

export const exportApi = {
  analysis: (analysisId) => longClient.get('/export/analysis', { params: { analysis_id: analysisId } }),
  interview: (sessionId) => longClient.get('/export/interview', { params: { session_id: sessionId } }),
  projectRewrite: (result) => longClient.post('/export/project-rewrite', { result }),
}
