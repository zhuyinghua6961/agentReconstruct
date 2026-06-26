const API_BASE = '/api/activity'
const SESSION_KEY = 'agentcode.activity.session_id.v1'

function readStoredToken() {
  return localStorage.getItem('token')
    || localStorage.getItem('agentcode.auth.token.v1')
    || ''
}

function ensureSessionId() {
  const existing = String(localStorage.getItem(SESSION_KEY) || '').trim()
  if (existing) {
    return existing
  }
  const generated = (typeof crypto !== 'undefined' && crypto.randomUUID)
    ? crypto.randomUUID()
    : `sess-${Date.now()}-${Math.random().toString(36).slice(2)}`
  localStorage.setItem(SESSION_KEY, generated)
  return generated
}

export function resetActivitySessionId() {
  localStorage.removeItem(SESSION_KEY)
}

function toInteractionIso(timestampMs) {
  const value = Number(timestampMs)
  if (!Number.isFinite(value) || value <= 0) {
    return null
  }
  return new Date(value).toISOString()
}

function parseInteractionMs(value) {
  const text = String(value || '').trim()
  if (!text) {
    return 0
  }
  const parsed = Date.parse(text)
  return Number.isFinite(parsed) ? parsed : 0
}

async function safeJson(response) {
  try {
    return await response.json()
  } catch {
    return {}
  }
}

export async function sendActivityHeartbeat({ finalize = false, lastInteractionAt = 0 } = {}) {
  const token = readStoredToken()
  if (!token) {
    return { success: false, error: 'not_authenticated' }
  }
  const sessionId = ensureSessionId()
  const payload = {
    session_id: sessionId,
    finalize: Boolean(finalize),
  }
  const interactionIso = toInteractionIso(lastInteractionAt)
  if (interactionIso) {
    payload.last_interaction_at = interactionIso
  }
  const response = await fetch(`${API_BASE}/heartbeat`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(payload),
    keepalive: finalize,
  })
  const result = await safeJson(response)
  const serverInteractionMs = parseInteractionMs(result?.data?.last_interaction_at)
  return {
    ...result,
    serverInteractionMs,
  }
}
