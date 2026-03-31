const ROUTE_LABELS = {
  kb_qa: '知识库问答',
  pdf_qa: 'PDF问答',
  tabular_qa: '表格问答',
  hybrid_qa: '混合文件问答',
}

const FILE_TYPE_LABELS = {
  pdf: 'PDF',
  excel: '表格',
  csv: '表格',
  xls: '表格',
  xlsx: '表格',
  table: '表格',
}

const FILE_STAGE_LABELS = {
  uploaded: '已上传',
  parsing: '解析中',
  parsed: '已解析',
  indexing: '索引中',
  ready: '就绪',
  failed: '失败',
}

function normalizeRoute(route) {
  return String(route || '').trim().toLowerCase()
}

function normalizeFileStage(candidate = {}) {
  const stage = String(candidate.processing_stage || '').trim().toLowerCase()
  if (FILE_STAGE_LABELS[stage]) return stage
  const parse = String(candidate.parse_status || '').trim().toLowerCase()
  const index = String(candidate.index_status || '').trim().toLowerCase()
  if (parse === 'failed' || index === 'failed') return 'failed'
  if (index === 'ready') return 'ready'
  if (index === 'indexing') return 'indexing'
  if (parse === 'parsed') return 'parsed'
  if (parse === 'parsing') return 'parsing'
  return 'uploaded'
}

function candidateLabel(candidate = {}) {
  const fileId = Number(candidate.file_id || 0)
  const displayNo = Number(candidate.display_no || candidate.file_no || 0)
  const title = String(candidate.file_name || candidate.title || '').trim() || `文件 ${displayNo > 0 ? `#${displayNo}` : `#${fileId || '?'}`}`
  const prefix = displayNo > 0 ? `#${displayNo}` : (fileId > 0 ? `#${fileId}` : '')
  const typed = FILE_TYPE_LABELS[String(candidate.file_type || '').trim().toLowerCase()] || '文件'
  const stage = FILE_STAGE_LABELS[normalizeFileStage(candidate)] || '处理中'
  const detail = `${typed}，${stage}`
  return prefix ? `${prefix} ${title}（${detail}）` : `${title}（${detail}）`
}

export function getRouteModeLabel(route, fallback = '') {
  return ROUTE_LABELS[normalizeRoute(route)] || String(fallback || '').trim()
}

export function mergeRoutingMetadata(existingMeta = {}, event = {}) {
  const metadata = existingMeta && typeof existingMeta === 'object' ? { ...existingMeta } : {}
  const next = event && typeof event === 'object' ? event : {}

  if (next.route) metadata.route = String(next.route)
  if (next.requested_mode) metadata.requested_mode = String(next.requested_mode)
  if (next.actual_mode) metadata.actual_mode = String(next.actual_mode)
  if (next.trace_id) metadata.trace_id = String(next.trace_id)
  if (next.source_scope !== undefined) metadata.source_scope = next.source_scope
  if (Array.isArray(next.selected_file_ids)) metadata.selected_file_ids = [...next.selected_file_ids]
  if (next.strategy) metadata.strategy = String(next.strategy)
  if (next.file_selection && typeof next.file_selection === 'object') {
    metadata.file_selection = { ...next.file_selection }
  }
  if (Array.isArray(next.route_reasons)) metadata.route_reasons = [...next.route_reasons]
  if (next.route_confidence !== undefined && Number.isFinite(Number(next.route_confidence))) {
    metadata.route_confidence = Number(next.route_confidence)
  }
  if (typeof next.classifier_used === 'boolean') metadata.classifier_used = next.classifier_used
  if (typeof next.needs_clarification === 'boolean') metadata.needs_clarification = next.needs_clarification
  if (Array.isArray(next.clarify_candidates)) metadata.clarify_candidates = next.clarify_candidates.map((item) => ({ ...item }))
  if (next.code) metadata.error_code = String(next.code)
  if (next.error) metadata.error_name = String(next.error)
  if (next.message) metadata.error_message = String(next.message)
  if (typeof next.retriable === 'boolean') metadata.retriable = next.retriable

  return metadata
}

export function buildRoutingErrorMarkdown({ code = '', message = '', metadata = {} } = {}) {
  const normalizedCode = String(code || '').trim().toUpperCase()
  const normalizedMessage = String(message || '').trim() || '处理失败'
  const routeLabel = getRouteModeLabel(metadata?.route)
  const selectedIds = Array.isArray(metadata?.selected_file_ids) ? metadata.selected_file_ids.filter(Boolean) : []
  const clarifyCandidates = Array.isArray(metadata?.clarify_candidates)
    ? metadata.clarify_candidates
    : (Array.isArray(metadata?.file_selection?.clarify_candidates) ? metadata.file_selection.clarify_candidates : [])

  const lines = []

  if (normalizedCode === 'FILE_SELECTION_CLARIFICATION_REQUIRED') {
    lines.push('需要你进一步明确本轮要使用的文件。', '', normalizedMessage)
    if (routeLabel) lines.push('', `当前意图路由：${routeLabel}`)
    if (clarifyCandidates.length > 0) {
      lines.push('', '候选文件：')
      clarifyCandidates.forEach((candidate) => {
        lines.push(`- ${candidateLabel(candidate)}`)
      })
    }
    return lines.join('\n')
  }

  if (normalizedCode === 'FILE_NOT_READY') {
    lines.push('目标文件还在处理中，当前不能开始问答。', '', normalizedMessage)
    if (routeLabel) lines.push('', `当前路由：${routeLabel}`)
    if (selectedIds.length > 0) lines.push(`已选文件：${selectedIds.map((id) => `#${id}`).join('、')}`)
    lines.push('', '建议：等待文件状态变为“就绪”后再重试。')
    return lines.join('\n')
  }

  if (normalizedCode === 'FILE_PROCESSING_FAILED') {
    lines.push('目标文件处理失败，当前不能开始问答。', '', normalizedMessage)
    if (routeLabel) lines.push('', `当前路由：${routeLabel}`)
    if (selectedIds.length > 0) lines.push(`已选文件：${selectedIds.map((id) => `#${id}`).join('、')}`)
    lines.push('', '建议：重新上传文件或改选其他文件。')
    return lines.join('\n')
  }

  if (normalizedCode === 'FILE_NOT_FOUND') {
    lines.push('目标文件不存在或已失效。', '', normalizedMessage)
    if (routeLabel) lines.push('', `当前路由：${routeLabel}`)
    if (selectedIds.length > 0) lines.push(`已选文件：${selectedIds.map((id) => `#${id}`).join('、')}`)
    lines.push('', '建议：重新选择文件后再试。')
    return lines.join('\n')
  }

  lines.push('处理失败', '', normalizedMessage)
  if (routeLabel) lines.push('', `当前路由：${routeLabel}`)
  return lines.join('\n')
}
