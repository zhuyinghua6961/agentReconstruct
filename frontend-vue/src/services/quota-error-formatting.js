import { normalizeUserQuotaList } from './quota-normalization.js'

const SYSTEM_UNAVAILABLE_CODES = new Set([
  'QUOTA_CONFIG_MISSING',
  'QUOTA_INTERNAL_UNAVAILABLE',
  'QUOTA_INTERNAL_INVALID_RESPONSE',
  'DB_UNAVAILABLE',
  'QUOTA_LOCK_TIMEOUT',
  'QUOTA_LOCK_UNAVAILABLE',
  'QUOTA_CHECK_ERROR',
  'QUOTA_GRANT_ERROR',
])

function normalizeCode(code) {
  return String(code || '').trim().toUpperCase()
}

function normalizeQuotaDetail(data) {
  if (!data || typeof data !== 'object') {
    return null
  }
  const [item] = normalizeUserQuotaList([data])
  return item || null
}

function buildUsageSummary(detail) {
  if (!detail) return ''
  const current = Number(detail.current || 0)
  const limit = Number(detail.limit || 0)
  const remaining = Number(detail.remaining || 0)
  if (!Number.isFinite(current) || !Number.isFinite(limit) || !Number.isFinite(remaining)) {
    return ''
  }
  return `已用 ${current} / ${limit}，剩余 ${remaining}`
}

function normalizeWindows(detail) {
  const items = Array.isArray(detail?.windows) ? detail.windows : []
  return items.map((item) => ({
    period: String(item?.period || ''),
    current: Number(item?.current || 0),
    limit: Number(item?.limit || 0),
    remaining: Number(item?.remaining || 0),
    resetTime: String(item?.reset_time || ''),
  }))
}

function resolveSystemDescription(code) {
  const normalized = normalizeCode(code)
  if (normalized === 'QUOTA_CONFIG_MISSING' || normalized === 'QUOTA_INTERNAL_UNAVAILABLE') {
    return '当前配额服务未就绪'
  }
  return '系统暂时无法确认当前额度状态'
}

export function isQuotaBlockingErrorCode(code) {
  const normalized = normalizeCode(code)
  return normalized === 'QUOTA_EXCEEDED' || SYSTEM_UNAVAILABLE_CODES.has(normalized)
}

export function buildQuotaErrorCardModel({
  code = '',
  message = '',
  data = null,
  featureTitle = '',
} = {}) {
  const normalizedCode = normalizeCode(code)
  if (!isQuotaBlockingErrorCode(normalizedCode)) {
    return null
  }

  const detail = normalizeQuotaDetail(data)
  const quotaType = String(detail?.quota_type || '').trim()
  const quotaName = String(detail?.quota_name || '').trim()
  const variant = normalizedCode === 'QUOTA_EXCEEDED' ? 'quota_exceeded' : 'system_unavailable'

  return {
    variant,
    featureTitle: String(featureTitle || '').trim(),
    headline: variant === 'quota_exceeded'
      ? `${String(featureTitle || '').trim()}次数已用完`
      : `${String(featureTitle || '').trim()}暂不可用`,
    description: variant === 'quota_exceeded'
      ? (quotaName ? `当前消耗配额：${quotaName}` : '')
      : resolveSystemDescription(normalizedCode),
    quotaType,
    quotaName,
    usageSummary: variant === 'quota_exceeded' ? buildUsageSummary(detail) : '',
    resetText: variant === 'quota_exceeded' ? String(detail?.reset_time || '') : '',
    windows: variant === 'quota_exceeded' ? normalizeWindows(detail) : [],
    action: {
      label: '去个人中心查看配额',
      to: '/profile',
    },
    rawMessage: String(message || '').trim(),
  }
}
