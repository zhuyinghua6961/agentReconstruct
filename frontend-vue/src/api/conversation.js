import { buildUrl, deleteJson, getJson, postJson } from './http';

const API_PREFIX = '/api/conversations';

export async function createConversation(title = '新会话') {
  return await postJson(`${API_PREFIX}`, { title });
}

export async function listConversations(page = 1, pageSize = 30) {
  const query = `?page=${encodeURIComponent(page)}&page_size=${encodeURIComponent(pageSize)}`;
  return await getJson(`${API_PREFIX}${query}`);
}

export async function getConversationDetail(conversationId) {
  return await getJson(`${API_PREFIX}/${encodeURIComponent(conversationId)}`);
}

export async function deleteConversation(conversationId) {
  return await deleteJson(`${API_PREFIX}/${encodeURIComponent(conversationId)}`);
}

export async function addConversationMessage(conversationId, message) {
  return await postJson(`${API_PREFIX}/${encodeURIComponent(conversationId)}/messages`, {
    message,
  });
}

export async function listConversationFiles(conversationId) {
  return await getJson(`${API_PREFIX}/${encodeURIComponent(conversationId)}/files`);
}

export function buildConversationFileDownloadUrl(conversationId, fileId) {
  const base = `${API_PREFIX}/${encodeURIComponent(conversationId)}/files/${encodeURIComponent(fileId)}/download`;
  if (typeof window === 'undefined') {
    return buildUrl(base);
  }
  const token = window.localStorage.getItem('agentcode.auth.token.v1') || '';
  if (!token) {
    return buildUrl(base);
  }
  return buildUrl(`${base}?token=${encodeURIComponent(token)}`);
}
