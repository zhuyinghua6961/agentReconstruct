export const CANONICAL_QUOTA_TYPES = ['ask_query', 'file_qa', 'file_view', 'doc_assist']

const CANONICAL_QUOTA_LABELS = {
  ask_query: '普通问答',
  file_qa: '文件问答',
  file_view: '查看原文',
  doc_assist: '文档辅助',
}

const QUOTA_TYPE_ALIASES = {
  ask_query: 'ask_query',
  kb_qa: 'ask_query',
  thinking_qa: 'ask_query',
  file_qa: 'file_qa',
  pdf_qa: 'file_qa',
  tabular_qa: 'file_qa',
  hybrid_qa: 'file_qa',
  file_view: 'file_view',
  doc_assist: 'doc_assist',
  pdf_summary: 'doc_assist',
  text_translate: 'doc_assist',
  reference_preview: 'doc_assist',
  literature_content: 'doc_assist',
  extract_pdf_text: 'doc_assist',
}

function canonicalOrder(quotaType) {
  const index = CANONICAL_QUOTA_TYPES.indexOf(String(quotaType || '').trim())
  return index >= 0 ? index : Number.MAX_SAFE_INTEGER
}

export function formatResetTime(resetHint) {
  const hint = String(resetHint || '').trim()
  if (hint === 'next_day_start') return '今日24:00'
  if (hint === 'next_week_start') return '下周开始'
  if (hint === 'next_month_start') return '下月1号00:00'
  if (hint.startsWith('next_custom_window_start:')) {
    const value = hint.split(':', 2)[1] || ''
    return value ? `${value} 00:00` : '自定义窗口重置'
  }
  if (hint === 'never') return '无限制'
  return hint || '未知'
}

function normalizeQuotaType(quotaType) {
  const normalized = String(quotaType || '').trim().toLowerCase()
  return QUOTA_TYPE_ALIASES[normalized] || ''
}

function normalizeQuotaName(quotaType, fallbackName) {
  return CANONICAL_QUOTA_LABELS[quotaType] || String(fallbackName || '').trim() || quotaType
}

function mergePreservingDefined(target, source) {
  for (const [key, value] of Object.entries(source || {})) {
    if (value === undefined) continue
    target[key] = value
  }
  return target
}

function sortCanonical(items) {
  return [...items].sort((left, right) => canonicalOrder(left?.quota_type) - canonicalOrder(right?.quota_type))
}

function normalizeQuotaItem(item) {
  const quotaType = normalizeQuotaType(item?.quota_type)
  if (!quotaType) return null
  return {
    ...item,
    quota_type: quotaType,
    quota_name: normalizeQuotaName(quotaType, item?.quota_name || item?.name),
  }
}

export function normalizeQuotaConfigList(items) {
  const deduped = new Map()
  for (const rawItem of Array.isArray(items) ? items : []) {
    const item = normalizeQuotaItem(rawItem)
    if (!item) continue
    const existing = deduped.get(item.quota_type)
    deduped.set(
      item.quota_type,
      existing ? mergePreservingDefined(existing, item) : { ...item },
    )
  }
  return sortCanonical([...deduped.values()])
}

function normalizeWindows(rawWindows, fallbackQuota = null) {
  const items = Array.isArray(rawWindows) ? rawWindows : []
  const normalized = []
  for (const item of items) {
    const period = String(item?.period || '').trim()
    if (!period) continue
    normalized.push({
      period,
      period_days: Number(item?.period_days || 0),
      current: Number(item?.current || 0),
      limit: Number(item?.limit || 0),
      remaining: Number(item?.remaining || 0),
      reset_time: formatResetTime(item?.reset_hint),
      allowed: item?.allowed !== false,
    })
  }
  if (normalized.length > 0) {
    return normalized
  }

  if (!fallbackQuota || !fallbackQuota.period) {
    return []
  }
  return [
    {
      period: String(fallbackQuota.period || 'none'),
      period_days: Number(fallbackQuota.period_days || 0),
      current: Number(fallbackQuota.current || 0),
      limit: Number(fallbackQuota.limit || 0),
      remaining: Number(fallbackQuota.remaining || 0),
      reset_time: formatResetTime(fallbackQuota.reset_hint),
      allowed: true,
    },
  ]
}

export function normalizeUserQuotaList(items) {
  const deduped = new Map()
  for (const rawItem of Array.isArray(items) ? items : []) {
    const item = normalizeQuotaItem(rawItem)
    if (!item) continue
    const normalized = {
      ...item,
      current: Number(item?.current || 0),
      limit: Number(item?.limit || 0),
      remaining: Number(item?.remaining || 0),
      period: item?.period || 'none',
      period_days: Number(item?.period_days || 0),
      reset_time: formatResetTime(item?.reset_hint || item?.reset_time),
      windows: normalizeWindows(item?.windows, item),
    }
    const existing = deduped.get(normalized.quota_type)
    deduped.set(
      normalized.quota_type,
      existing ? mergePreservingDefined(existing, normalized) : normalized,
    )
  }
  return sortCanonical([...deduped.values()])
}

export function normalizeMyQuotaData(rawData) {
  if (!rawData || typeof rawData !== 'object') {
    return {}
  }

  const items = Array.isArray(rawData.quotas)
    ? rawData.quotas
    : Object.entries(rawData).map(([quota_type, value]) => ({ quota_type, ...(value || {}) }))

  const normalizedItems = normalizeUserQuotaList(items)
  const normalized = {}
  for (const item of normalizedItems) {
    normalized[item.quota_type] = {
      name: item.quota_name,
      period: item.period || 'none',
      period_days: Number(item.period_days || 0),
      current: Number(item.current || 0),
      limit: Number(item.limit || 0),
      remaining: Number(item.remaining || 0),
      reset_time: item.reset_time || '未知',
      windows: Array.isArray(item.windows) ? item.windows : [],
    }
  }
  return normalized
}
