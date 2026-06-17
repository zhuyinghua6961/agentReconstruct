const MAX_FILENAME_LENGTH = 120

export function sanitizeDownloadFilename(value, fallback = 'document') {
  const normalized = String(value || '')
    .trim()
    .replace(/[\\/:*?"<>|]+/g, '_')
    .replace(/\s+/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_+|_+$/g, '')

  const base = normalized || fallback
  if (base.length <= MAX_FILENAME_LENGTH) {
    return base
  }
  return base.slice(0, MAX_FILENAME_LENGTH)
}

export function downloadTextFile({ content, filename }) {
  const blob = new Blob(['\ufeff', String(content || '')], {
    type: 'text/markdown;charset=utf-8',
  })
  const objectUrl = window.URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = objectUrl
  link.download = filename
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  window.URL.revokeObjectURL(objectUrl)
}
