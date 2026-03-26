import { buildUrl, getJson, postForm, postJson } from './http';
import { streamSseJson } from '../utils/sse';

const API_PREFIX = '/api';

function normalizeChatHistory(chatHistory = []) {
  return (Array.isArray(chatHistory) ? chatHistory : [])
    .map((item) => {
      const roleRaw = String(item?.role || '').trim().toLowerCase();
      const role = roleRaw === 'bot' ? 'assistant' : roleRaw;
      const content = String(item?.content || '');
      return { role, content };
    })
    .filter((item) => ['user', 'assistant', 'system'].includes(item.role) && item.content.trim().length > 0);
}

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
  mode = 'thinking',
}) {
  const token =
    typeof window !== 'undefined'
      ? window.localStorage.getItem('agentcode.auth.token.v1') || ''
      : '';
  const headers = { 'Content-Type': 'application/json' };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  const normalizedMode = String(mode || 'thinking').trim().toLowerCase();
  const askPath = ['fast', 'thinking', 'patent'].includes(normalizedMode)
    ? `${API_PREFIX}/${normalizedMode}/ask_stream`
    : `${API_PREFIX}/ask_stream`;

  const body = {
    question,
    chat_history: normalizeChatHistory(chatHistory),
    requested_mode: ['fast', 'thinking', 'patent'].includes(normalizedMode) ? normalizedMode : 'fast',
  };
  if (conversationId) {
    body.conversation_id = conversationId;
  }
  const options = {};
  if (typeof usePdf === 'boolean') options.use_pdf = usePdf;
  if (pdfPath) options.pdf_path = pdfPath;
  if (typeof useGenerationDriven === 'boolean') options.use_generation_driven = useGenerationDriven;
  if (Object.keys(options).length > 0) {
    body.options = options;
  }
  if (usePdf || pdfPath) {
    body.pdf_context = {
      legacy_use_pdf: Boolean(usePdf),
      legacy_pdf_path: String(pdfPath || ''),
    };
  }

  const response = await fetch(buildUrl(askPath), {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
    signal,
  });

  if (!response.ok) {
    throw new Error(`HTTP ${response.status} while requesting ask_stream`);
  }

  await streamSseJson({ response, onEvent });
}
