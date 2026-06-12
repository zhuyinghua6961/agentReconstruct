export function getPersonnelImportCreatedCount(summary = {}) {
  if (typeof summary?.created === 'number') {
    return summary.created
  }
  if (typeof summary?.success === 'number') {
    return summary.success
  }
  return 0
}

export function getPersonnelImportSuccessCount(summary = {}) {
  return getPersonnelImportCreatedCount(summary)
}

export function getPersonnelImportUpdatedCount(summary = {}) {
  return Number(summary?.updated || 0)
}

export function getPersonnelImportSkippedCount(summary = {}) {
  return Number(summary?.skipped || 0)
}

export function normalizePersonnelImportResultStatus(status) {
  const normalized = String(status || '').trim().toLowerCase()
  if (normalized === 'created' || normalized === 'success') {
    return 'created'
  }
  if (normalized === 'updated') {
    return 'updated'
  }
  if (normalized === 'failed' || normalized === 'skipped') {
    return normalized
  }
  return ''
}

export function filterPersonnelImportDetails(details = [], filterStatus = 'all') {
  if (filterStatus === 'all') {
    return details
  }
  return details.filter(item => normalizePersonnelImportResultStatus(item?.status) === filterStatus)
}

export function getPersonnelImportStatusClass(status) {
  return {
    success: 'status-success',
    created: 'status-success',
    updated: 'status-updated',
    failed: 'status-failed',
    skipped: 'status-skipped',
  }[status] || ''
}

export function getPersonnelImportResultText(status) {
  return {
    success: '成功',
    created: '新增',
    updated: '更新',
    failed: '失败',
    skipped: '跳过',
  }[status] || status
}
