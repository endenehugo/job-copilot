import client from './client'

export { notifyError } from './client'

export const conversationApi = {
  create: (data) => client.post('/conversation/create', data),
  list: (limit = 50) => client.get('/conversation/list', { params: { limit } }),
  detail: (id) => client.get('/conversation/detail', { params: { conversation_id: id } }),
  chat: (data) => client.post('/conversation/chat', data),
  chatStream: (data) => client.post('/conversation/chat/stream', data),
  uploadImage: (formData) => client.post('/conversation/image/upload', formData),
  remove: (conversationId) => client.post('/conversation/delete', { conversation_id: conversationId }),
}

export const documentApi = {
  upload: (formData) => client.post('/document/upload', formData),
  remove: (documentId) => client.post('/document/delete', { document_id: documentId }),
}

export const jobApi = {
  analyze: (data) => client.post('/job/analyze', data),
  analyzeScreenshot: (data) => client.post('/job/analyze-from-screenshot', data),
  latest: (conversationId) =>
    client.get('/job/analysis/latest', { params: { conversation_id: conversationId } }),
}

export const resumeApi = {
  rewrite: (data) => client.post('/resume/project/rewrite', data),
  versions: (conversationId) =>
    client.get('/resume/versions/list', { params: { conversation_id: conversationId } }),
  versionDetail: (versionId) =>
    client.get('/resume/versions/detail', { params: { version_id: versionId } }),
  compare: (conversationId) =>
    client.get('/resume/versions/compare', { params: { conversation_id: conversationId } }),
}

export const interviewApi = {
  start: (data) => client.post('/interview/start', data),
  answer: (data) => client.post('/interview/answer', data),
  list: (conversationId) => client.get('/interview/list', { params: { conversation_id: conversationId } }),
  detail: (sessionId) => client.get('/interview/detail', { params: { session_id: sessionId } }),
}

export const knowledgeApi = {
  rebuild: () => client.post('/knowledge/rebuild'),
  query: (query, k = 4) => client.get('/knowledge/query', { params: { query, k } }),
  categories: () => client.get('/knowledge/categories'),
  status: () => client.get('/knowledge/status'),
  submit: (content) => client.post('/knowledge/entries', { content }),
  listEntries: (status = 'approved', limit = 50) =>
    client.get('/knowledge/entries', { params: { status, limit } }),
  removeEntry: (entryId) => client.delete(`/knowledge/entries/${entryId}`),
  selfExpand: (count = 3) => client.post('/knowledge/self-expand', { count }),
}

export const exportApi = {
  analysis: (analysisId) => client.get('/export/analysis', { params: { analysis_id: analysisId } }),
  interview: (sessionId) => client.get('/export/interview', { params: { session_id: sessionId } }),
  projectRewrite: (result) => client.post('/export/project-rewrite', { result }),
}
