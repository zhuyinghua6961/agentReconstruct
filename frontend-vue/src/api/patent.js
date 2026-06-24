import { buildUrl, getJson, postJson } from './http.js';

const API_PREFIX = '/api';

function appendParam(params, key, value) {
  if (value === undefined || value === null || value === '') {
    return;
  }
  params.set(key, String(value));
}

export async function searchPatent(options = {}) {
  const params = new URLSearchParams();
  appendParam(params, 'query', options.query);
  appendParam(params, 'query_type', options.queryType);
  appendParam(params, 'sources', options.sources);
  if (Number(options.limit) > 0) {
    appendParam(params, 'limit', options.limit);
  }
  const query = params.toString();
  const path = query
    ? `${API_PREFIX}/patent_search?${query}`
    : `${API_PREFIX}/patent_search`;
  return await getJson(path);
}

export async function searchPatentPost(payload = {}) {
  return await postJson(`${API_PREFIX}/patent_search`, payload);
}

export async function getPatentAbstract(patentId) {
  const encoded = encodeURIComponent(String(patentId || '').trim());
  return await getJson(`${API_PREFIX}/patent/original/${encoded}?section=abstract`);
}

export function buildPatentPdfUrl(patentId) {
  const encoded = encodeURIComponent(String(patentId || '').trim());
  const token = readPdfAuthToken();
  const base = buildUrl(`${API_PREFIX}/patent/original/${encoded}?section=fulltext`);
  if (!token) {
    return base;
  }
  return `${base}${base.includes('?') ? '&' : '?'}token=${encodeURIComponent(token)}`;
}

function readPdfAuthToken() {
  const storage = typeof window !== 'undefined'
    ? window.localStorage
    : globalThis.localStorage;
  if (!storage) {
    return '';
  }
  return (
    storage.getItem('token') ||
    storage.getItem('agentcode.auth.token.v1') ||
    ''
  );
}

export { fetchPdfDocumentByUrl } from './literature.js';
