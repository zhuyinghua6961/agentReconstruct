const API_BASE = import.meta.env.VITE_API_BASE_URL || '';
const TOKEN_KEYS = ['agentcode.auth.token.v1', 'token'];

function withBase(path) {
  if (!API_BASE) {
    return path;
  }
  return `${API_BASE}${path}`;
}

function readAuthToken() {
  if (typeof window === 'undefined') {
    return '';
  }
  for (const key of TOKEN_KEYS) {
    const token = window.localStorage.getItem(key) || '';
    if (token) {
      return token;
    }
  }
  return '';
}

function buildHeaders({ includeJson = true, extra = {}, auth = true } = {}) {
  const headers = {};
  if (includeJson) {
    headers['Content-Type'] = 'application/json';
  }
  if (auth) {
    const token = readAuthToken();
    if (token) {
      headers.Authorization = `Bearer ${token}`;
    }
  }
  return { ...headers, ...(extra || {}) };
}

export async function getJson(path, options = {}) {
  const { headers, auth = true } = options;
  const resp = await fetch(withBase(path), {
    method: 'GET',
    headers: buildHeaders({ includeJson: true, extra: headers, auth }),
  });
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}: ${path}`);
  }
  return await resp.json();
}

export async function postJson(path, payload, options = {}) {
  const { headers, auth = true } = options;
  const resp = await fetch(withBase(path), {
    method: 'POST',
    headers: buildHeaders({ includeJson: true, extra: headers, auth }),
    body: JSON.stringify(payload ?? {}),
  });
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}: ${path}`);
  }
  return await resp.json();
}

export async function putJson(path, payload, options = {}) {
  const { headers, auth = true } = options;
  const resp = await fetch(withBase(path), {
    method: 'PUT',
    headers: buildHeaders({ includeJson: true, extra: headers, auth }),
    body: JSON.stringify(payload ?? {}),
  });
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}: ${path}`);
  }
  return await resp.json();
}

export async function postForm(path, formData, options = {}) {
  const { headers, auth = true } = options;
  const resp = await fetch(withBase(path), {
    method: 'POST',
    headers: buildHeaders({ includeJson: false, extra: headers, auth }),
    body: formData,
  });
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}: ${path}`);
  }
  return await resp.json();
}

export async function deleteJson(path, options = {}) {
  const { headers, auth = true } = options;
  const resp = await fetch(withBase(path), {
    method: 'DELETE',
    headers: buildHeaders({ includeJson: true, extra: headers, auth }),
  });
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}: ${path}`);
  }
  return await resp.json();
}

export function buildUrl(path) {
  return withBase(path);
}
