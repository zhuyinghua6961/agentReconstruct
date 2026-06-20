import { buildUrl, getJson, postJson } from './http.js';

const API_PREFIX = '/api';

function appendParam(params, key, value) {
  if (value === undefined || value === null || value === '') {
    return;
  }
  params.set(key, String(value));
}

export async function searchLiterature(options = {}) {
  const params = new URLSearchParams();
  appendParam(params, 'query', options.query);
  appendParam(params, 'query_type', options.queryType);
  appendParam(params, 'match_mode', options.matchMode);
  appendParam(params, 'sources', options.sources);
  if (Number(options.limit) > 0) {
    appendParam(params, 'limit', options.limit);
  }
  const query = params.toString();
  const path = query
    ? `${API_PREFIX}/literature_search?${query}`
    : `${API_PREFIX}/literature_search`;
  return await getJson(path);
}

export async function searchLiteraturePost(payload = {}) {
  return await postJson(`${API_PREFIX}/literature_search`, payload);
}

export async function getLiteratureContent(doi) {
  const encoded = encodeURIComponent(String(doi || '').trim());
  return await getJson(`${API_PREFIX}/literature_content?doi=${encoded}`);
}

export async function getReferencePreview(dois, options = {}) {
  const values = Array.isArray(dois)
    ? dois.map((item) => String(item || '').trim()).filter(Boolean)
    : [];
  if (values.length === 0) {
    return { items: [], count: 0, requested_count: 0, max_items: 30, truncated: false };
  }
  const maxItems = Number(options?.maxItems) > 0 ? Number(options.maxItems) : 30;
  return await postJson(`${API_PREFIX}/reference_preview`, { doi: values, max_items: maxItems });
}

export async function checkPdfAvailability(doi) {
  const encodedPath = encodeDoiPath(doi);
  return await getJson(`${API_PREFIX}/check_pdf/${encodedPath}`);
}

function readPdfAuthToken() {
  const storage = typeof window !== 'undefined'
    ? window.localStorage
    : globalThis.localStorage
  if (!storage) {
    return '';
  }
  return (
    storage.getItem('token') ||
    storage.getItem('agentcode.auth.token.v1') ||
    ''
  );
}

function encodeDoiPath(doi) {
  return String(doi || '')
    .split('/')
    .map((item) => encodeURIComponent(item))
    .join('/');
}

function resolveDocumentUrl(pathOrUrl) {
  const value = String(pathOrUrl || '').trim();
  if (!value) {
    return '';
  }
  if (value.startsWith('http://') || value.startsWith('https://')) {
    return value;
  }
  return buildUrl(value);
}

async function readErrorPayload(response) {
  try {
    return await response.json();
  } catch {
    return {};
  }
}

export async function fetchPdfDocument(doi) {
  const encodedPath = encodeDoiPath(doi);
  return await fetchPdfDocumentByUrl(`${API_PREFIX}/view_pdf/${encodedPath}`);
}

export async function fetchPdfDocumentByUrl(pathOrUrl) {
  const resolvedUrl = resolveDocumentUrl(pathOrUrl);
  const token = readPdfAuthToken();
  const response = await fetch(resolvedUrl, {
    method: 'GET',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  const contentType = String(response.headers.get('content-type') || '').toLowerCase();

  if (response.ok && contentType.includes('application/pdf')) {
    const blob = await response.blob();
    return {
      ok: true,
      blobUrl: URL.createObjectURL(blob),
      contentType,
    };
  }

  const errorPayload = await readErrorPayload(response);
  return {
    ok: false,
    errorPayload: {
      ...(errorPayload && typeof errorPayload === 'object' ? errorPayload : {}),
      status: Number(response.status || 0),
      contentType,
    },
  };
}

export function buildPdfViewUrl(doi) {
  const encodedPath = encodeDoiPath(doi);
  const token = readPdfAuthToken();
  const base = buildUrl(`${API_PREFIX}/view_pdf/${encodedPath}`);
  if (!token) {
    return base;
  }
  return `${base}${base.includes('?') ? '&' : '?'}token=${encodeURIComponent(token)}`;
}
