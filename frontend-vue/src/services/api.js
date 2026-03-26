// API service layer for the UI-aligned pages.
// This adapter normalizes current backend contracts to the shape expected by the UI.

function resolveBackendBase() {
  const explicit = String(import.meta.env.VITE_API_BASE_URL || '').trim();
  if (explicit) {
    return explicit.replace(/\/$/, '');
  }
  return '';
}

const API_BASE = resolveBackendBase();
const V1 = '/api';

function readStoredToken() {
  return localStorage.getItem('token')
    || localStorage.getItem('agentcode.auth.token.v1')
    || '';
}

function clearStoredAuth() {
  localStorage.removeItem('token');
  localStorage.removeItem('user');
  localStorage.removeItem('agentcode.auth.token.v1');
  localStorage.removeItem('agentcode.auth.user.v1');
}

function authHeaders(includeJson = true) {
  const token = readStoredToken();
  const headers = {};
  if (includeJson) headers['Content-Type'] = 'application/json';
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

function handleApiError(error, response) {
  if (error?.code === 'ACCOUNT_DISABLED') {
    clearStoredAuth();
    alert('您的账号已被停用，请联系管理员');
    window.location.href = '/login';
    return;
  }
  if (
    error?.code === 'TOKEN_INVALID' ||
    error?.code === 'TOKEN_MISSING' ||
    response?.status === 401
  ) {
    clearStoredAuth();
    if (!window.location.pathname.includes('/login')) {
      window.location.href = '/login';
    }
  }
}

async function fetchWithErrorHandling(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    handleApiError(payload, response);
    const error = new Error(payload?.error || `HTTP ${response.status}`);
    error.status = Number(response.status || 0);
    error.code = payload?.code || '';
    error.payload = payload;
    throw error;
  }
  return response;
}

async function requestJson(url, options = {}) {
  const response = await fetchWithErrorHandling(url, options);
  return await response.json();
}

function unwrapData(payload) {
  if (payload && payload.success && payload.data !== undefined) {
    return payload.data;
  }
  return payload || {};
}

function normalizeConversationSummary(item) {
  return {
    conversation_id: Number(item?.conversation_id || 0),
    title: String(item?.title || '新对话'),
    message_count: Number(item?.message_count || 0),
    created_at: item?.created_at || new Date().toISOString(),
    updated_at: item?.updated_at || new Date().toISOString(),
  };
}

function normalizeStepStatus(status) {
  const raw = String(status || '').trim().toLowerCase();
  if (['processing', 'in_progress', 'running', 'pending'].includes(raw)) return 'processing';
  if (['success', 'succeeded', 'completed', 'complete', 'done', 'ok'].includes(raw)) return 'success';
  if (['error', 'failed', 'fail', 'failure'].includes(raw)) return 'error';
  return 'processing';
}

function normalizeSteps(rawSteps) {
  if (!Array.isArray(rawSteps)) return [];
  const byStep = new Map();
  const orderedSteps = [];

  rawSteps.forEach((item, idx) => {
    const step = String(item?.step || `step_${idx + 1}`).trim() || `step_${idx + 1}`;
    const normalized = {
      step,
      message: String(item?.message || item?.content || step),
      title: String(item?.title || ''),
      detail: String(item?.detail || ''),
      status: normalizeStepStatus(item?.status),
      data: item?.data && typeof item.data === 'object' ? item.data : undefined,
    };
    if (item?.error) normalized.error = String(item.error);
    if (item?.legacy_type) normalized.legacyType = String(item.legacy_type);
    if (item?.updatedAt || item?.updated_at) normalized.updatedAt = String(item.updatedAt || item.updated_at);

    if (byStep.has(step)) {
      const existing = byStep.get(step);
      byStep.set(step, { ...existing, ...normalized });
      const pos = orderedSteps.findIndex((s) => s.step === step);
      if (pos >= 0) orderedSteps[pos] = byStep.get(step);
      return;
    }
    byStep.set(step, normalized);
    orderedSteps.push(normalized);
  });

  return orderedSteps;
}

function normalizeReferenceLinks(rawLinks, references = []) {
  const normalized = [];
  const seen = new Set();

  const append = (item) => {
    if (!item) return;
    const doi = String(typeof item === 'string' ? item : item?.doi || '').trim();
    if (!doi) return;
    const key = doi.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    const pdfUrl = String(
      (typeof item === 'object' && (item?.pdfUrl || item?.pdf_url || item?.url))
      || `${V1}/view_pdf/${encodeURIComponent(doi)}`
    ).trim();
    normalized.push({ doi, pdfUrl });
  };

  if (Array.isArray(rawLinks)) {
    rawLinks.forEach(append);
  }
  if (normalized.length === 0 && Array.isArray(references)) {
    references.forEach((item) => append(item?.doi || item));
  }
  return normalized;
}

function normalizeDoiLocations(rawLocations) {
  if (!rawLocations || typeof rawLocations !== 'object') return {};
  return Object.entries(rawLocations).reduce((acc, [doi, locations]) => {
    const cleanDoi = String(doi || '').trim();
    if (!cleanDoi) return acc;
    acc[cleanDoi] = Array.isArray(locations)
      ? locations.map((item) => (item && typeof item === 'object' ? { ...item } : item)).filter(Boolean)
      : [];
    return acc;
  }, {});
}

function normalizeMessage(item) {
  const metadata = item?.metadata && typeof item.metadata === 'object' ? { ...item.metadata } : {};
  const refsRaw = Array.isArray(item?.references)
    ? item.references
    : (Array.isArray(metadata.references) ? metadata.references : []);
  const references = refsRaw
    .map((ref) => {
      if (typeof ref === 'string') {
        return { doi: ref.trim(), title: '' };
      }
      return {
        doi: String(ref?.doi || '').trim(),
        title: String(ref?.title || ''),
      };
    })
    .filter((ref) => ref.doi);

  const rawMode = String(item?.queryMode || item?.query_mode || metadata.query_mode || metadata.queryMode || '').trim();
  const queryModeMap = {
    fast: '快速模式',
    thinking: '思考模式',
    patent: '专利模式',
    neo4j: '知识图谱',
    community: '社区分析',
    literature: '文献检索',
    tabular_qa: '表格问答',
    hybrid_qa: '混合文件问答',
    tabular: '表格问答',
  };
  const queryMode = rawMode ? (queryModeMap[rawMode] || rawMode) : '';
  const referenceLinks = normalizeReferenceLinks(
    item?.referenceLinks
    || item?.reference_links
    || item?.pdfLinks
    || item?.pdf_links
    || metadata?.referenceLinks
    || metadata?.reference_links
    || metadata?.pdfLinks
    || metadata?.pdf_links,
    references,
  );
  const steps = normalizeSteps(item?.steps || metadata.steps);
  const doiLocations = normalizeDoiLocations(
    item?.doiLocations || item?.doi_locations || metadata?.doiLocations || metadata?.doi_locations
  );

  return {
    role: String(item?.role || 'assistant'),
    content: String(item?.content || ''),
    timestamp: item?.timestamp || item?.created_at || new Date().toISOString(),
    metadata: {
      ...metadata,
      references,
      ...(referenceLinks.length > 0 ? { reference_links: referenceLinks, pdf_links: referenceLinks } : {}),
      ...(steps.length > 0 ? { steps } : {}),
      ...(rawMode ? { query_mode: rawMode } : {}),
      ...(Object.keys(doiLocations).length > 0 ? { doi_locations: doiLocations } : {}),
    },
    queryMode,
    references,
    referenceLinks,
    doiLocations,
    steps,
    stepsCollapsed: false,
    isComplete: true,
  };
}

function normalizeUploadedFile(item) {
  const fileType = String(item?.file_type || '');
  return {
    id: Number(item?.id || 0),
    file_id: Number(item?.id || 0),
    file_no: Number(item?.file_no || 0),
    display_no: Number(item?.display_no || 0),
    file_type: fileType,
    file_name: String(item?.file_name || ''),
    title: String(item?.file_name || ''),
    local_path: String(item?.local_path || ''),
    storage_ref: String(item?.storage_ref || ''),
    content_type: String(item?.content_type || ''),
    size_bytes: Number(item?.size_bytes || 0),
    file_status: String(item?.file_status || 'active'),
    parse_status: String(item?.parse_status || 'uploaded'),
    index_status: String(item?.index_status || 'pending'),
    processing_stage: String(item?.processing_stage || 'uploaded'),
    status_updated_at: item?.status_updated_at || item?.created_at || new Date().toISOString(),
    last_error: String(item?.last_error || ''),
    file_meta: item?.file_meta && typeof item.file_meta === 'object' ? item.file_meta : {},
    uploaded_at: item?.created_at || new Date().toISOString(),
  };
}

function asPdfList(files) {
  return (files || [])
    .filter((f) => f.file_type === 'pdf')
    .map((f) => ({
      file_id: f.file_id,
      file_no: f.file_no,
      display_no: f.display_no,
      pdf_title: f.file_name || f.title || 'PDF',
      pdf_path: f.storage_ref || f.local_path || '',
      file_hash: '',
      uploaded_at: f.uploaded_at,
      parse_status: f.parse_status || 'uploaded',
      index_status: f.index_status || 'pending',
      processing_stage: f.processing_stage || 'uploaded',
      status_updated_at: f.status_updated_at || f.uploaded_at,
      last_error: f.last_error || '',
      file_meta: f.file_meta || {},
    }));
}

function asExcelList(files) {
  return (files || [])
    .filter((f) => f.file_type === 'excel')
    .map((f) => ({
      file_id: f.file_id,
      file_no: f.file_no,
      display_no: f.display_no,
      excel_title: f.file_name || f.title || 'Excel',
      excel_path: f.storage_ref || f.local_path || '',
      uploaded_at: f.uploaded_at,
      parse_status: f.parse_status || 'uploaded',
      index_status: f.index_status || 'pending',
      processing_stage: f.processing_stage || 'uploaded',
      status_updated_at: f.status_updated_at || f.uploaded_at,
      last_error: f.last_error || '',
      file_meta: f.file_meta || {},
    }));
}

export const api = {
  // ==================== Conversation ====================

  async createConversation(_userId, title = '新对话') {
    const payload = await requestJson(`${API_BASE}${V1}/conversations`, {
      method: 'POST',
      headers: authHeaders(true),
      body: JSON.stringify({ title }),
    });
    if (!payload?.success) {
      throw new Error(payload?.error || '创建对话失败');
    }
    const data = unwrapData(payload);
    return {
      conversation_id: Number(data?.conversation_id || 0),
      title: String(data?.title || title),
      created_at: data?.created_at || new Date().toISOString(),
      updated_at: data?.updated_at || new Date().toISOString(),
      message_count: Number(data?.message_count || 0),
    };
  },

  async getConversationList(_userId, page = 1, pageSize = 20) {
    const payload = await requestJson(
      `${API_BASE}${V1}/conversations?page=${encodeURIComponent(page)}&page_size=${encodeURIComponent(pageSize)}`,
      {
        method: 'GET',
        headers: authHeaders(false),
      }
    );
    if (!payload?.success) {
      throw new Error(payload?.error || '获取对话列表失败');
    }
    const data = unwrapData(payload);
    return {
      conversations: (data?.conversations || []).map(normalizeConversationSummary),
      pagination: {
        total: Number(data?.total_count || 0),
        page: Number(data?.page || page),
        page_size: Number(data?.page_size || pageSize),
      },
    };
  },

  async getConversationDetail(conversationId, _userId) {
    const payload = await requestJson(`${API_BASE}${V1}/conversations/${encodeURIComponent(conversationId)}`, {
      method: 'GET',
      headers: authHeaders(false),
    });
    if (!payload?.success) {
      throw new Error(payload?.error || '获取对话详情失败');
    }
    const data = unwrapData(payload);
    const files = (data?.uploaded_files || []).map(normalizeUploadedFile);
    return {
      conversation_id: Number(data?.conversation_id || conversationId),
      title: String(data?.title || '新对话'),
      created_at: data?.created_at || new Date().toISOString(),
      updated_at: data?.updated_at || new Date().toISOString(),
      message_count: Number(data?.message_count || 0),
      messages: (data?.messages || []).map(normalizeMessage),
      uploaded_files: files,
      pdf_list: asPdfList(files),
      excel_list: asExcelList(files),
    };
  },

  async addMessage(conversationId, _userId, message) {
    const payload = await requestJson(`${API_BASE}${V1}/conversations/${encodeURIComponent(conversationId)}/messages`, {
      method: 'POST',
      headers: authHeaders(true),
      body: JSON.stringify({ message }),
    });
    return payload;
  },

  async updateConversationTitle(_conversationId, _userId, _title) {
    const payload = await requestJson(`${API_BASE}${V1}/conversations/${encodeURIComponent(_conversationId)}/title`, {
      method: 'PUT',
      headers: authHeaders(true),
      body: JSON.stringify({ title: _title }),
    })
    if (!payload?.success) {
      throw new Error(payload?.error || '更新对话标题失败')
    }
    const data = unwrapData(payload)
    return {
      conversation_id: Number(data?.conversation_id || _conversationId),
      title: String(data?.title || _title || '新对话'),
      created_at: data?.created_at || new Date().toISOString(),
      updated_at: data?.updated_at || new Date().toISOString(),
      message_count: Number(data?.message_count || 0),
    }
  },

  async deleteConversation(conversationId, _userId) {
    const payload = await requestJson(`${API_BASE}${V1}/conversations/${encodeURIComponent(conversationId)}`, {
      method: 'DELETE',
      headers: authHeaders(false),
    });
    return payload;
  },

  async removePdfFromConversation(conversationId, pdfId) {
    const payload = await requestJson(
      `${API_BASE}${V1}/conversations/${encodeURIComponent(conversationId)}/files/${encodeURIComponent(pdfId)}`,
      {
        method: 'DELETE',
        headers: authHeaders(false),
      }
    );
    if (!payload?.success) {
      throw new Error(payload?.error || '删除PDF失败');
    }
    return payload;
  },

  async uploadExcel(file, conversationId) {
    const formData = new FormData();
    formData.append('file', file);
    if (conversationId) formData.append('conversation_id', String(conversationId));

    const response = await fetchWithErrorHandling(`${API_BASE}${V1}/upload_excel`, {
      method: 'POST',
      headers: authHeaders(false),
      body: formData,
    });
    const payload = await response.json();
    if (payload?.error) {
      return { success: false, error: payload.error };
    }
    const resolvedFileId = Number(payload?.file_id || 0);
    if (!resolvedFileId) {
      return { success: false, error: '上传成功但未生成文件记录，请重试' };
    }
    const document = {
      file_id: resolvedFileId,
      title: String(payload?.filename || file?.name || 'excel'),
      file_path: String(payload?.filepath || ''),
      storage_ref: String(payload?.storage_ref || ''),
      parse_status: String(payload?.parse_status || 'uploaded'),
      index_status: String(payload?.index_status || 'pending'),
      processing_stage: String(payload?.processing_stage || 'uploaded'),
    };
    return {
      success: true,
      file_id: document.file_id,
      title: document.title,
      filepath: document.file_path,
      storage_ref: document.storage_ref,
      document,
    };
  },

  async removeExcelFromConversation(conversationId, excelId) {
    const payload = await requestJson(
      `${API_BASE}${V1}/conversations/${encodeURIComponent(conversationId)}/files/${encodeURIComponent(excelId)}`,
      {
        method: 'DELETE',
        headers: authHeaders(false),
      }
    );
    if (!payload?.success) {
      throw new Error(payload?.error || '删除文件失败');
    }
    return payload;
  },

  // ==================== Knowledge ====================

  async getKbInfo() {
    return await requestJson(`${API_BASE}${V1}/kb_info`, {
      method: 'GET',
      headers: authHeaders(false),
    });
  },

  async *askStream(question, chatHistory = [], conversationId = null, pdfContext = null, signal = undefined, mode = 'thinking') {
    const normalizedMode = String(mode || 'thinking').trim().toLowerCase();
    const body = {
      question,
      chat_history: chatHistory.slice(-10),
      requested_mode: ['fast', 'thinking', 'patent'].includes(normalizedMode) ? normalizedMode : 'fast',
    };
    if (conversationId) body.conversation_id = conversationId;
    if (pdfContext) body.pdf_context = pdfContext;

    const askPath = ['fast', 'thinking', 'patent'].includes(normalizedMode)
      ? `${V1}/${normalizedMode}/ask_stream`
      : `${V1}/ask_stream`;

    const response = await fetch(`${API_BASE}${askPath}`, {
      method: 'POST',
      headers: authHeaders(true),
      body: JSON.stringify(body),
      signal,
    });

    const contentType = String(response.headers.get('content-type') || '').toLowerCase();
    if (!response.ok && !contentType.includes('text/event-stream')) {
      const payload = await response.json().catch(() => ({}));
      handleApiError(payload, response);
      const error = new Error(payload?.message || payload?.error || `HTTP ${response.status}`);
      error.status = Number(response.status || 0);
      error.code = payload?.code || '';
      error.payload = payload;
      throw error;
    }

    const reader = response.body?.getReader();
    if (!reader) {
      throw new Error('Streaming not supported');
    }
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split('\n\n');
      buffer = frames.pop() || '';
      for (const frame of frames) {
        const lines = frame
          .split('\n')
          .map((line) => line.trim())
          .filter(Boolean);
        if (lines.length === 0) continue;
        const dataLines = lines
          .filter((line) => line.startsWith('data:'))
          .map((line) => line.slice(5).trim());
        if (dataLines.length === 0) continue;
        try {
          yield JSON.parse(dataLines.join('\n'));
        } catch {
          // ignore malformed frame
        }
      }
    }
  },

  async ask(question, chatHistory = [], mode = 'thinking') {
    const normalizedMode = String(mode || 'thinking').trim().toLowerCase();
    const askPath = ['fast', 'thinking', 'patent'].includes(normalizedMode)
      ? `${V1}/${normalizedMode}/ask`
      : `${V1}/ask`;
    const payload = await requestJson(`${API_BASE}${askPath}`, {
      method: 'POST',
      headers: authHeaders(true),
      body: JSON.stringify({
        question,
        chat_history: chatHistory.slice(-10),
        requested_mode: ['fast', 'thinking', 'patent'].includes(normalizedMode) ? normalizedMode : 'fast',
      }),
    });
    return payload;
  },

  async translate(texts) {
    const payload = await requestJson(`${API_BASE}${V1}/translate`, {
      method: 'POST',
      headers: authHeaders(true),
      body: JSON.stringify({ texts }),
    });
    const nested = payload?.data && typeof payload.data === 'object' ? payload.data : null;
    const topTranslations = Array.isArray(payload?.translations) ? payload.translations : null;
    const dataTranslations = Array.isArray(nested?.translations) ? nested.translations : null;
    const normalizedTranslations = dataTranslations || topTranslations || [];

    return {
      ...payload,
      success: payload?.success !== false,
      data: {
        ...(nested || {}),
        translations: normalizedTranslations,
        count:
          Number(
            nested?.count ??
              payload?.count ??
              normalizedTranslations.length
          ) || 0,
      },
      translations: normalizedTranslations,
      count:
        Number(
          nested?.count ??
            payload?.count ??
            normalizedTranslations.length
        ) || 0,
    };
  },

  viewPdf(doi) {
    return `${API_BASE}${V1}/view_pdf/${encodeURIComponent(String(doi || ''))}`;
  },

  async summarizePdf(doi) {
    return await requestJson(`${API_BASE}${V1}/summarize_pdf/${encodeURIComponent(String(doi || ''))}`, {
      method: 'POST',
      headers: authHeaders(false),
    });
  },

  async uploadPdf(file, conversationId, onProgress) {
    const formData = new FormData();
    formData.append('file', file);
    if (conversationId) formData.append('conversation_id', String(conversationId));

    return await new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();

      xhr.upload.addEventListener('progress', (event) => {
        if (!event.lengthComputable) return;
        const percent = Math.round((event.loaded / event.total) * 100);
        onProgress?.(percent);
      });

      xhr.addEventListener('load', () => {
        if (xhr.status !== 200) {
          reject(new Error(`HTTP ${xhr.status}`));
          return;
        }
        let payload = {};
        try {
          payload = JSON.parse(xhr.responseText || '{}');
        } catch {
          reject(new Error('响应解析失败'));
          return;
        }
        if (payload?.error) {
          resolve({ success: false, error: payload.error });
          return;
        }
        const resolvedFileId = Number(payload?.file_id || 0);
        if (!resolvedFileId) {
          resolve({ success: false, error: '上传成功但未生成文件记录，请重试' });
          return;
        }
        resolve({
          success: true,
          document: {
            file_id: resolvedFileId,
            title: String(payload?.filename || file?.name || 'pdf'),
            file_path: String(payload?.filepath || ''),
            hash: '',
            parse_status: String(payload?.parse_status || 'uploaded'),
            index_status: String(payload?.index_status || 'pending'),
            processing_stage: String(payload?.processing_stage || 'uploaded'),
          },
          ...payload,
        });
      });

      xhr.addEventListener('error', () => reject(new Error('网络错误')));
      xhr.open('POST', `${API_BASE}${V1}/upload_pdf`);
      const token = readStoredToken();
      if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
      xhr.send(formData);
    });
  },
};
