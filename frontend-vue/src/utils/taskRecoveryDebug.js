function readEnvFlag(name) {
  try {
    const env = (typeof import.meta !== 'undefined' && import.meta?.env) ? import.meta.env : {}
    return String(env?.[name] ?? '').trim()
  } catch {
    return ''
  }
}

function readStorageFlag(key) {
  try {
    return String(globalThis?.localStorage?.getItem?.(key) ?? '').trim()
  } catch {
    return ''
  }
}

function isTruthyFlag(value) {
  return ['1', 'true', 'yes', 'on', 'debug'].includes(String(value || '').trim().toLowerCase())
}

function isAssistantRole(message) {
  const role = String(message?.role || '').trim().toLowerCase()
  return role === 'assistant' || role === 'bot'
}

function normalizeTaskId(value) {
  return String(value || '').trim()
}

function normalizeSeq(value) {
  const numeric = Number(value)
  return Number.isFinite(numeric) && numeric >= 0 ? Math.trunc(numeric) : 0
}

function summarizeAssistantMessage(message = {}) {
  const metadata = message?.metadata && typeof message.metadata === 'object' ? message.metadata : {}
  return {
    taskId: normalizeTaskId(metadata.task_id || message.task_id),
    status: String(message?.status || metadata?.status || '').trim().toLowerCase(),
    terminalStatus: String(message?.terminalStatus || message?.terminal_status || metadata?.terminal_status || '').trim().toLowerCase(),
    lastSeq: normalizeSeq(message?.last_seq ?? metadata?.last_seq),
    doneSeen: message?.doneSeen ?? message?.done_seen ?? metadata?.done_seen,
    contentLength: String(message?.content || '').length,
    isComplete: message?.isComplete,
  }
}

export function summarizeTaskRecoveryDetail(detail = {}, taskId = '') {
  const normalizedTaskId = normalizeTaskId(taskId)
  const activeTask = detail?.active_task && typeof detail.active_task === 'object' ? detail.active_task : null
  const messages = Array.isArray(detail?.messages) ? detail.messages : []
  const matchingAssistant = messages.find((message) => {
    if (!isAssistantRole(message)) return false
    if (!normalizedTaskId) return true
    const metadata = message?.metadata && typeof message.metadata === 'object' ? message.metadata : {}
    return normalizeTaskId(metadata.task_id || message.task_id) === normalizedTaskId
  }) || null

  return {
    activeTask: activeTask
      ? {
          taskId: normalizeTaskId(activeTask.task_id || activeTask.request_id),
          status: String(activeTask.status || '').trim().toLowerCase(),
          lastSeq: normalizeSeq(activeTask.last_seq),
          replayAvailable: activeTask.replay_available !== false,
        }
      : null,
    messageCount: messages.length,
    matchingAssistant: matchingAssistant ? summarizeAssistantMessage(matchingAssistant) : null,
  }
}

export function summarizeTaskEventBatch(events = []) {
  const list = Array.isArray(events) ? events : []
  const first = list[0] || null
  const last = list[list.length - 1] || null
  let contentChars = 0
  const typeCounts = {}

  list.forEach((event) => {
    const type = String(event?.type || '').trim().toLowerCase() || 'unknown'
    typeCounts[type] = Number(typeCounts[type] || 0) + 1
    if (type === 'content') {
      contentChars += String(event?.content || event?.delta || '').length
    }
  })

  return {
    count: list.length,
    firstSeq: first ? normalizeSeq(first.seq) : 0,
    lastSeq: last ? normalizeSeq(last.seq) : 0,
    firstType: first ? String(first.type || '').trim().toLowerCase() : '',
    lastType: last ? String(last.type || '').trim().toLowerCase() : '',
    contentChars,
    typeCounts,
  }
}

export function createTaskRecoveryDebugLogger(options = {}) {
  const envFlagName = String(options?.envFlagName || 'VITE_TASK_RECOVERY_DEBUG').trim()
  const storageKey = String(options?.storageKey || 'agentcode.task-recovery-debug').trim()
  const sink = typeof options?.sink === 'function'
    ? options.sink
    : (entry) => {
        console.info(`[task-recovery:${entry.scope}]`, entry.payload)
      }

  function isEnabled() {
    return isTruthyFlag(readEnvFlag(envFlagName)) || isTruthyFlag(readStorageFlag(storageKey))
  }

  function log(scope, payload = {}) {
    if (!isEnabled()) return
    sink({
      scope: String(scope || '').trim() || 'unknown',
      payload,
    })
  }

  return {
    isEnabled,
    log,
  }
}
