import client, { longClient, BULK_TIMEOUT } from './client'

export { notifyError } from './client'

// 分组依据：普通 CRUD 走 30 秒；上传 / LLM 分析 / 导出走 120 秒；
// 知识库重建与自主扩充实测 1~2 分钟，单独给 300 秒。

export const conversationApi = {
  create: (data) => client.post('/conversation/create', data),
  list: (limit = 50) => client.get('/conversation/list', { params: { limit } }),
  detail: (id) => client.get('/conversation/detail', { params: { conversation_id: id } }),
  chat: (data) => longClient.post('/conversation/chat', data),
  chatStream: (data) => client.post('/conversation/chat/stream', data),
  uploadImage: (formData) => longClient.post('/conversation/image/upload', formData),
  remove: (conversationId) => client.post('/conversation/delete', { conversation_id: conversationId }),
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
}

export const resumeApi = {
  rewrite: (data) => longClient.post('/resume/project/rewrite', data),
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
