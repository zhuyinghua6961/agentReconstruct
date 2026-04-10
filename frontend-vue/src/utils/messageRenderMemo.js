const memoCache = new WeakMap()

function normalizeString(value) {
  return String(value || '')
}

function normalizeStep(step = {}) {
  return {
    step: normalizeString(step?.step),
    status: normalizeString(step?.status),
    title: normalizeString(step?.title),
    detail: normalizeString(step?.detail),
    error: normalizeString(step?.error),
  }
}

function normalizeReference(reference = {}) {
  if (typeof reference === 'string') return reference
  return {
    doi: normalizeString(reference?.doi),
    title: normalizeString(reference?.title),
  }
}

function normalizeReferenceLink(referenceLink = {}) {
  if (typeof referenceLink === 'string') return referenceLink
  return {
    doi: normalizeString(referenceLink?.doi),
    pdfUrl: normalizeString(referenceLink?.pdfUrl || referenceLink?.pdf_url),
  }
}

function normalizeDoiLocations(doiLocations = {}) {
  if (!doiLocations || typeof doiLocations !== 'object') return {}
  return Object.keys(doiLocations)
    .sort()
    .reduce((acc, doi) => {
      acc[doi] = Array.isArray(doiLocations[doi])
        ? doiLocations[doi].map((item) => ({
            page: item?.page ?? '',
            section: normalizeString(item?.section),
            chunk_id: normalizeString(item?.chunk_id),
            sentence_index: item?.sentence_index ?? '',
          }))
        : []
      return acc
    }, {})
}

function normalizeTerminalStatus(message = {}) {
  const metadata = message?.metadata && typeof message.metadata === 'object' ? message.metadata : {}
  return normalizeString(
    message?.terminalStatus
    ?? message?.terminal_status
    ?? message?.status
    ?? metadata?.terminal_status
    ?? metadata?.status
    ?? metadata?.streaming_terminal_event
    ?? '',
  ).trim().toLowerCase()
}

function normalizeDoneSeen(message = {}) {
  const metadata = message?.metadata && typeof message.metadata === 'object' ? message.metadata : {}
  return Boolean(message?.doneSeen ?? message?.done_seen ?? metadata?.done_seen)
}

function buildRenderSnapshot(message = {}) {
  return {
    role: normalizeString(message?.role),
    content: normalizeString(message?.content),
    queryMode: normalizeString(message?.queryMode),
    isComplete: Boolean(message?.isComplete),
    doneSeen: normalizeDoneSeen(message),
    terminalStatus: normalizeTerminalStatus(message),
    stepsCollapsed: Boolean(message?.stepsCollapsed),
    stepsRef: Array.isArray(message?.steps) ? message.steps : null,
    referencesRef: Array.isArray(message?.references) ? message.references : null,
    referenceLinksRef: Array.isArray(message?.referenceLinks) ? message.referenceLinks : null,
    doiLocationsRef: message?.doiLocations && typeof message.doiLocations === 'object' ? message.doiLocations : null,
  }
}

function hasSameRenderSnapshot(left, right) {
  if (!left || !right) return false
  return left.role === right.role
    && left.content === right.content
    && left.queryMode === right.queryMode
    && left.isComplete === right.isComplete
    && left.doneSeen === right.doneSeen
    && left.terminalStatus === right.terminalStatus
    && left.stepsCollapsed === right.stepsCollapsed
    && left.stepsRef === right.stepsRef
    && left.referencesRef === right.referencesRef
    && left.referenceLinksRef === right.referenceLinksRef
    && left.doiLocationsRef === right.doiLocationsRef
}

export function buildMessageRenderMemoKey(message = {}) {
  if (!message || typeof message !== 'object') {
    return JSON.stringify({})
  }

  const snapshot = buildRenderSnapshot(message)
  const existing = memoCache.get(message)
  if (existing && hasSameRenderSnapshot(existing.snapshot, snapshot)) {
    return existing.key
  }

  const signature = JSON.stringify({
    role: snapshot.role,
    content: snapshot.content,
    queryMode: snapshot.queryMode,
    isComplete: snapshot.isComplete,
    doneSeen: snapshot.doneSeen,
    terminalStatus: snapshot.terminalStatus,
    stepsCollapsed: snapshot.stepsCollapsed,
    steps: Array.isArray(snapshot.stepsRef) ? snapshot.stepsRef.map(normalizeStep) : [],
    references: Array.isArray(snapshot.referencesRef) ? snapshot.referencesRef.map(normalizeReference) : [],
    referenceLinks: Array.isArray(snapshot.referenceLinksRef) ? snapshot.referenceLinksRef.map(normalizeReferenceLink) : [],
    doiLocations: normalizeDoiLocations(snapshot.doiLocationsRef),
  })

  memoCache.set(message, { snapshot, key: signature })
  return signature
}
