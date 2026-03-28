export function hasLocationSimilarity(hint) {
  const value = Number(hint?.similarity)
  return Number.isFinite(value)
}

export function resolveLocationBadge(hint) {
  if (hasLocationSimilarity(hint)) {
    return `${Math.round(Number(hint.similarity) * 100)}%`
  }
  const page = Number(hint?.page)
  if (Number.isFinite(page) && page > 0) {
    return `P${page}`
  }
  const section = String(hint?.section || '').trim()
  if (section) {
    return '章节'
  }
  const chunkIndex = Number(hint?.chunk_index)
  if (Number.isFinite(chunkIndex) && chunkIndex >= 0) {
    return '片段'
  }
  return '定位'
}

export function resolveLocationTitle(hint) {
  const section = String(hint?.section || '').trim()
  if (section) {
    return section
  }
  const chunkIndex = Number(hint?.chunk_index)
  if (Number.isFinite(chunkIndex) && chunkIndex >= 0) {
    return `片段 #${chunkIndex + 1}`
  }
  const source = resolveLocationSource(hint)
  if (source) {
    return '证据片段'
  }
  return '未知位置'
}

export function resolveLocationSentence(hint) {
  return String(hint?.answer_sentence || hint?.sentence || '').trim()
}

export function resolveLocationSource(hint) {
  return String(hint?.source_text || hint?.source_preview || hint?.evidence_text || '').trim()
}
