function normalizeTaskStatus(status) {
  const raw = String(status || '').trim().toLowerCase()
  if (raw === 'completed') return 'completed'
  if (raw === 'cancelled') return 'canceled'
  if (['queued', 'admitted', 'running', 'failed', 'canceled', 'expired'].includes(raw)) {
    return raw
  }
  return ''
}

function normalizeLastSeq(value, fallback = 0) {
  const normalized = Number(value)
  if (Number.isFinite(normalized) && normalized >= 0) {
    return Math.max(Math.trunc(normalized), Math.trunc(Number(fallback) || 0))
  }
  return Math.max(0, Math.trunc(Number(fallback) || 0))
}

function deriveStatusFromEvents(events = [], fallback = '') {
  let status = normalizeTaskStatus(fallback)
  ;(Array.isArray(events) ? events : []).forEach((event) => {
    const type = String(event?.type || '').trim().toLowerCase()
    if (type === 'state') {
      status = normalizeTaskStatus(event?.status) || status
      return
    }
    if (type === 'done') {
      status = 'completed'
      return
    }
    if (type === 'error') {
      status = 'failed'
    }
  })
  return status
}

export function isRecoverableTaskStatus(status) {
  return ['queued', 'admitted', 'running'].includes(normalizeTaskStatus(status))
}

export function normalizeTaskReplayCursor(taskSummary = {}, cachedLastSeq = 0) {
  const taskId = String(taskSummary?.task_id || taskSummary?.request_id || '').trim()
  const status = deriveStatusFromEvents([], taskSummary?.status)
  const replayAvailable = taskSummary?.replay_available !== false
  const lastSeq = normalizeLastSeq(taskSummary?.last_seq, cachedLastSeq)
  const recoverable = Boolean(taskId) && isRecoverableTaskStatus(status)
  const terminal = Boolean(taskId) && !recoverable

  return {
    taskId,
    status,
    lastSeq,
    recoverable,
    replayAvailable: Boolean(replayAvailable),
    terminal,
  }
}

export function advanceTaskReplayCursor(cursor = {}, events = []) {
  const nextStatus = deriveStatusFromEvents(events, cursor?.status)
  const lastSeq = (Array.isArray(events) ? events : []).reduce((maxSeq, event) => {
    const seq = Number(event?.seq)
    if (!Number.isFinite(seq) || seq < 0) return maxSeq
    return Math.max(maxSeq, Math.trunc(seq))
  }, normalizeLastSeq(cursor?.lastSeq, 0))

  return normalizeTaskReplayCursor(
    {
      task_id: cursor?.taskId,
      status: nextStatus,
      last_seq: lastSeq,
      replay_available: cursor?.replayAvailable !== false,
    },
    lastSeq,
  )
}

export function shouldFallBackToConversationTruth(cursor = {}) {
  if (!cursor || typeof cursor !== 'object') return true
  if (!String(cursor.taskId || '').trim()) return true
  if (cursor.terminal) return true
  return cursor.replayAvailable === false
}
