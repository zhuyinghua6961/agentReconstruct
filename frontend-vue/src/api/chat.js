import { buildUrl, getJson, postForm, postJson } from './http';
import { streamSseJson } from '../utils/sse';

const API_PREFIX = '/api/v1';

export async function getKbInfo() {
  return await getJson(`${API_PREFIX}/kb_info`);
}

export async function refreshKb() {
  return await postJson(`${API_PREFIX}/refresh_kb`, {});
}

export async function clearCache() {
  return await postJson(`${API_PREFIX}/clear_cache`, {});
}

export async function clearPdf() {
  return await postJson(`${API_PREFIX}/clear_pdf`, {});
}

export async function uploadPdf(file, conversationId = null) {
  const form = new FormData();
  form.append('file', file);
  if (conversationId) {
    form.append('conversation_id', String(conversationId));
  }
  return await postForm(`${API_PREFIX}/upload_pdf`, form);
}

export async function uploadExcel(file, conversationId = null) {
  const form = new FormData();
  form.append('file', file);
  if (conversationId) {
    form.append('conversation_id', String(conversationId));
  }
  return await postForm(`${API_PREFIX}/upload_excel`, form);
}

export async function streamAsk({
  question,
  chatHistory,
  usePdf,
  pdfPath,
  useGenerationDriven,
  conversationId,
  signal,
  onEvent,
}) {
  const token =
    typeof window !== 'undefined'
      ? window.localStorage.getItem('agentcode.auth.token.v1') || ''
      : '';
  const headers = { 'Content-Type': 'application/json' };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  const response = await fetch(buildUrl(`${API_PREFIX}/ask_stream`), {
    method: 'POST',
    headers,
    body: JSON.stringify({
      question,
      chat_history: chatHistory,
      use_pdf: usePdf,
      pdf_path: pdfPath,
      use_generation_driven: useGenerationDriven,
      conversation_id: conversationId || undefined,
    }),
    signal,
  });

  if (!response.ok) {
    throw new Error(`HTTP ${response.status} while requesting ask_stream`);
  }

  await streamSseJson({ response, onEvent });
}
