const SUPPORTED_DRAFT_FILE_TYPES = new Map([
  ['pdf', 'pdf'],
  ['xlsx', 'excel'],
  ['xls', 'excel'],
  ['csv', 'excel'],
])

function sanitizeDraftIdPart(value) {
  return String(value || '')
    .replace(/[^a-zA-Z0-9]/g, '')
    .slice(0, 12)
}

export function resolveDraftFileType(fileName) {
  const ext = String(fileName || '')
    .trim()
    .toLowerCase()
    .split('.')
    .pop()
  return SUPPORTED_DRAFT_FILE_TYPES.get(ext) || ''
}

export function defaultPromptForDraftFileType(fileType) {
  const type = String(fileType || '').trim().toLowerCase()
  if (type === 'pdf') return '请帮我总结一下这篇文献的主要内容'
  if (type === 'excel') return '请帮我分析一下这个表格的数据'
  return ''
}

export function buildPendingDraftFile(file, options = {}) {
  const now = typeof options.now === 'function' ? options.now() : new Date().toISOString()
  const randomValue = typeof options.random === 'function'
    ? options.random()
    : Math.random().toString(36).slice(2, 8)
  const timestampPart = String(now || new Date().toISOString()).replace(/\D/g, '')
  const randomPart = sanitizeDraftIdPart(randomValue) || 'file'
  const name = String(file?.name || '').trim()
  const type = String(options.type || resolveDraftFileType(name)).trim().toLowerCase()

  return {
    draftId: `draft_${timestampPart}_${randomPart}`,
    file,
    type,
    name,
    size: Number(file?.size || 0) || 0,
    createdAt: now,
    uploadStatus: 'pending',
    error: '',
  }
}

export function buildPendingDraftFileItem(draft, index = 0) {
  const type = String(draft?.type || '').trim().toLowerCase()
  return {
    type: `draft-${type || 'file'}`,
    draftId: String(draft?.draftId || ''),
    title: String(draft?.name || '待发送文件'),
    size: Number(draft?.size || 0) || 0,
    displayLabel: `待传${Number(index || 0) + 1}`,
    statusLabel: String(draft?.uploadStatus || '').trim() === 'failed' ? '上传失败' : '待发送',
  }
}
