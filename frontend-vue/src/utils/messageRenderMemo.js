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

export function buildMessageRenderMemoKey(message = {}) {
  if (!message || typeof message !== 'object') {
    return JSON.stringify({})
  }

  const signature = JSON.stringify({
    role: normalizeString(message?.role),
    content: normalizeString(message?.content),
    queryMode: normalizeString(message?.queryMode),
    isComplete: Boolean(message?.isComplete),
    stepsCollapsed: Boolean(message?.stepsCollapsed),
    steps: Array.isArray(message?.steps) ? message.steps.map(normalizeStep) : [],
    references: Array.isArray(message?.references) ? message.references.map(normalizeReference) : [],
    referenceLinks: Array.isArray(message?.referenceLinks) ? message.referenceLinks.map(normalizeReferenceLink) : [],
    doiLocations: normalizeDoiLocations(message?.doiLocations),
  })

  const existing = memoCache.get(message)
  if (existing && existing.signature === signature) {
    return existing.key
  }

  memoCache.set(message, { signature, key: signature })
  return signature
}
