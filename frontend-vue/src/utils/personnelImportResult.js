export function getPersonnelImportSuccessCount(summary = {}) {
  if (typeof summary?.success === 'number') {
    return summary.success
  }
  return Number(summary?.created || 0) + Number(summary?.updated || 0)
}

export function getPersonnelImportSkippedCount(summary = {}) {
  return Number(summary?.skipped || 0)
}

export function normalizePersonnelImportResultStatus(status) {
  const normalized = String(status || '').trim().toLowerCase()
  if (normalized === 'created' || normalized === 'updated' || normalized === 'success') {
    return 'success'
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
    updated: 'status-success',
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
