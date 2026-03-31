import { buildUrl, getJson, postJson } from './http.js';

const API_PREFIX = '/api';

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

async function readErrorPayload(response) {
  try {
    return await response.json();
  } catch {
    return {};
  }
}

export async function fetchPdfDocument(doi) {
  const encodedPath = encodeDoiPath(doi);
  const token = readPdfAuthToken();
  const response = await fetch(buildUrl(`${API_PREFIX}/view_pdf/${encodedPath}`), {
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
