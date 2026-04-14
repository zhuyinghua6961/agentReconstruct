const PATENT_FILE_ROUTE_HINTS = new Set(['pdf_qa', 'tabular_qa', 'hybrid_qa'])
const STRUCTURED_CONTENT_ROLES = new Set(['preview', 'final'])
const STRUCTURED_CONTENT_PHASES = new Set(['start', 'delta', 'end', 'snapshot'])

export const PATENT_STREAM_CAPABILITY_HEADER = 'X-Patent-Stream-Capability'
export const PATENT_STREAM_CAPABILITY_VALUE = 'preview_v1'

function normalizePositiveIds(values = []) {
  if (!Array.isArray(values)) return []
  const unique = []
  const seen = new Set()
  values.forEach((value) => {
    const numeric = Number(value || 0)
    if (!Number.isInteger(numeric) || numeric <= 0 || seen.has(numeric)) return
    seen.add(numeric)
    unique.push(numeric)
  })
  return unique
}

function normalizePreviewBucket(bucket = {}, contentStreamId = '', contentSource = '') {
  return {
    contentStreamId: String(bucket?.contentStreamId || contentStreamId || '').trim(),
    contentSource: String(bucket?.contentSource || contentSource || '').trim(),
    content: String(bucket?.content || ''),
    contentPhase: String(bucket?.contentPhase || '').trim(),
    completed: bucket?.completed === true,
  }
}

function normalizePatentStreamingState(value = null) {
  const previewStreamsRaw = value?.previewStreams && typeof value.previewStreams === 'object'
    ? value.previewStreams
    : {}
  const previewOrderRaw = Array.isArray(value?.previewOrder) ? value.previewOrder : []
  const previewStreams = {}
  const previewOrder = []

  previewOrderRaw.forEach((streamId) => {
    const normalizedStreamId = String(streamId || '').trim()
    if (!normalizedStreamId || previewOrder.includes(normalizedStreamId)) return
    previewOrder.push(normalizedStreamId)
  })

  Object.entries(previewStreamsRaw).forEach(([streamId, bucket]) => {
    const normalizedStreamId = String(streamId || '').trim()
    if (!normalizedStreamId) return
    previewStreams[normalizedStreamId] = normalizePreviewBucket(bucket, normalizedStreamId)
    if (!previewOrder.includes(normalizedStreamId)) {
      previewOrder.push(normalizedStreamId)
    }
  })

  return {
    capabilityEnabled: value?.capabilityEnabled === true,
    finalSeen: value?.finalSeen === true,
    finalSource: String(value?.finalSource || '').trim(),
    finalPhase: String(value?.finalPhase || '').trim(),
    previewOrder,
    previewStreams,
  }
}

function resolvePatentStreamingState(input = null) {
  if (input?.previewStreams && typeof input.previewStreams === 'object') {
    return normalizePatentStreamingState(input)
  }
  if (input?.patentStreaming && typeof input.patentStreaming === 'object') {
    return normalizePatentStreamingState(input.patentStreaming)
  }
  if (input?.metadata?.patent_streaming && typeof input.metadata.patent_streaming === 'object') {
    return normalizePatentStreamingState(input.metadata.patent_streaming)
  }
  return normalizePatentStreamingState(null)
}

function normalizeStructuredContentEvent(event = {}) {
  if (String(event?.type || '').trim().toLowerCase() !== 'content') return null

  const contentRole = String(event?.content_role || '').trim().toLowerCase()
  if (!STRUCTURED_CONTENT_ROLES.has(contentRole)) return null

  const contentSource = String(event?.content_source || '').trim().toLowerCase()
  if (!contentSource) return null

  const rawPhase = String(event?.content_phase || '').trim().toLowerCase()
  const contentPhase = STRUCTURED_CONTENT_PHASES.has(rawPhase)
    ? rawPhase
    : (contentRole === 'final' ? 'snapshot' : '')
  if (!contentPhase) return null

  const contentStreamId = String(
    event?.content_stream_id || (contentRole === 'final' ? 'final:answer' : '')
  ).trim()
  if (contentRole === 'preview' && !contentStreamId) return null

  return {
    contentRole,
    contentSource,
    contentStreamId,
    contentPhase,
    replaceStream: event?.replace_stream === true,
    content: String(event?.content || event?.delta || ''),
  }
}

export function buildPatentStreamingCapability({ mode = 'thinking', pdfContext = null } = {}) {
  const normalizedMode = String(mode || '').trim().toLowerCase()
  const context = pdfContext && typeof pdfContext === 'object' ? pdfContext : {}
  const hasFileHints = (
    normalizePositiveIds(context?.selected_ids).length > 0
    || normalizePositiveIds(context?.newly_uploaded_ids).length > 0
    || normalizePositiveIds(context?.all_available_ids).length > 0
    || PATENT_FILE_ROUTE_HINTS.has(String(context?.last_turn_route || '').trim().toLowerCase())
  )
  const enabled = normalizedMode === 'patent' && hasFileHints

  return {
    enabled,
    headers: enabled
      ? { [PATENT_STREAM_CAPABILITY_HEADER]: PATENT_STREAM_CAPABILITY_VALUE }
      : {},
    requestOptions: enabled
      ? { patent_stream_capability: PATENT_STREAM_CAPABILITY_VALUE }
      : {},
  }
}

export function isStructuredPatentContentEvent(event = {}) {
  return normalizeStructuredContentEvent(event) !== null
}

export function getPatentPreviewStreams(input = null) {
  const state = resolvePatentStreamingState(input)
  return state.previewOrder
    .map((streamId) => state.previewStreams[streamId])
    .filter(Boolean)
    .map((bucket) => ({ ...bucket }))
}

export function isPatentFinalAnswerPending(message = null) {
  const state = resolvePatentStreamingState(message)
  if (!state.capabilityEnabled) return false
  if (state.finalSeen) return false
  if (getPatentPreviewStreams(state).length === 0) return false
  if (String(message?.content || '').trim()) return false
  if (message?.isComplete === true || message?.doneSeen === true || message?.metadata?.done_seen === true) return false
  return true
}

export function buildPatentStreamingMessagePatch(message = {}, stateInput = null) {
  const state = resolvePatentStreamingState(stateInput)
  const metadata = message?.metadata && typeof message.metadata === 'object'
    ? { ...message.metadata }
    : {}
  metadata.patent_streaming = state
  return {
    patentStreaming: state,
    metadata,
  }
}

export function reducePatentStreamingState(stateOrMessage = null, event = {}) {
  const contentEvent = normalizeStructuredContentEvent(event)
  const state = resolvePatentStreamingState(stateOrMessage)

  if (!contentEvent) {
    return {
      handled: false,
      state,
      mainContentMode: 'legacy',
      content: String(event?.content || event?.delta || ''),
      replaceContent: false,
    }
  }

  const nextState = normalizePatentStreamingState({
    ...state,
    capabilityEnabled: true,
  })

  if (contentEvent.contentRole === 'final') {
    nextState.finalSeen = true
    nextState.finalSource = contentEvent.contentSource
    nextState.finalPhase = contentEvent.contentPhase
    return {
      handled: true,
      state: nextState,
      mainContentMode: 'final',
      content: contentEvent.content,
      replaceContent: contentEvent.replaceStream,
    }
  }

  const streamId = contentEvent.contentStreamId
  const existingBucket = nextState.previewStreams[streamId]
    ? normalizePreviewBucket(nextState.previewStreams[streamId], streamId, contentEvent.contentSource)
    : normalizePreviewBucket(null, streamId, contentEvent.contentSource)
  const nextBucket = {
    ...existingBucket,
    contentSource: contentEvent.contentSource,
    contentPhase: contentEvent.contentPhase,
  }

  if (!nextState.previewOrder.includes(streamId)) {
    nextState.previewOrder = [...nextState.previewOrder, streamId]
  }

  if (contentEvent.contentPhase === 'snapshot') {
    nextBucket.content = contentEvent.content
  } else {
    if (contentEvent.replaceStream) {
      nextBucket.content = ''
    }
    if (contentEvent.content) {
      nextBucket.content += contentEvent.content
    }
  }

  nextBucket.completed = contentEvent.contentPhase === 'snapshot' || contentEvent.contentPhase === 'end'
  nextState.previewStreams = {
    ...nextState.previewStreams,
    [streamId]: nextBucket,
  }

  return {
    handled: true,
    state: nextState,
    mainContentMode: 'preview',
    content: '',
    replaceContent: false,
  }
}
