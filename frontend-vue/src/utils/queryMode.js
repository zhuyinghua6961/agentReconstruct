import { getRouteModeLabel } from './routingStatus.js'

export const ASK_MODE_LABELS = {
  fast: '快速模式',
  thinking: '深度模式',
  patent: '专利模式',
  graph_kb: '知识图谱',
  neo4j: '知识图谱',
  community: '社区分析',
  literature: '文献检索',
  tabular_qa: '表格问答',
  hybrid_qa: '混合文件问答',
  tabular: '表格问答',
}

export function formatQueryModeLabel(mode) {
  const raw = String(mode || '').trim()
  const key = raw.toLowerCase()
  return ASK_MODE_LABELS[key] || raw
}

export function resolveActualQueryModeRaw(event = {}, metadata = {}) {
  const next = event && typeof event === 'object' ? event : {}
  const meta = metadata && typeof metadata === 'object' ? metadata : {}
  return String(
    next.actual_mode
    || meta.actual_mode
    || next.query_mode
    || next.queryMode
    || meta.query_mode
    || meta.queryMode
    || ''
  ).trim()
}

export function resolveActualQueryModeLabel(event = {}, metadata = {}, options = {}) {
  const rawMode = resolveActualQueryModeRaw(event, metadata)
  if (rawMode) return formatQueryModeLabel(rawMode)
  if (options?.allowRouteFallback === false) return ''
  return getRouteModeLabel(event?.route || metadata?.route || '')
}
