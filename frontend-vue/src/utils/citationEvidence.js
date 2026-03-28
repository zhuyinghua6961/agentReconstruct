function normalizeDoi(value) {
  return String(value || '').trim().toLowerCase()
}

export function buildCitationLocationsForDoi({ doi, doiLocations, references }) {
  const normalizedDoi = normalizeDoi(doi)
  if (!normalizedDoi) return []

  const direct = doiLocations && typeof doiLocations === 'object'
    ? doiLocations[doi] || doiLocations[normalizedDoi] || []
    : []
  if (Array.isArray(direct) && direct.length > 0) {
    return direct
  }

  const items = Array.isArray(references) ? references : []
  const match = items.find((item) => normalizeDoi(item?.doi) === normalizedDoi)
  if (!match || typeof match !== 'object') {
    return []
  }

  const fallback = {}
  const page = Number(match.page)
  if (Number.isFinite(page) && page > 0) {
    fallback.page = page
  }
  const section = String(match.section_name || match.section || '').trim()
  if (section) {
    fallback.section = section
  }
  const chunkIndex = Number(match.chunk_index)
  if (Number.isFinite(chunkIndex)) {
    fallback.chunk_index = chunkIndex
  }
  const evidenceText = String(match.evidence_text || match.sample_text || '').trim()
  if (evidenceText) {
    fallback.source_text = evidenceText
    fallback.source_preview = evidenceText
  }
  const confidence = String(match.locator_confidence || '').trim()
  if (confidence) {
    fallback.confidence = confidence
  }
  return Object.keys(fallback).length > 0 ? [fallback] : []
}
