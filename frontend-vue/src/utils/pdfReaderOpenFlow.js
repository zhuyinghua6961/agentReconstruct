import { buildQuotaErrorCardModel } from '../services/quota-error-formatting.js'

function isAuthFailure(code, status) {
  const normalizedCode = String(code || '').trim().toUpperCase()
  return status === 401 || ['TOKEN_MISSING', 'TOKEN_INVALID', 'USER_NOT_FOUND'].includes(normalizedCode)
}

export function releasePdfBlobUrl(blobUrl, revokeObjectURL = URL.revokeObjectURL) {
  const value = String(blobUrl || '').trim()
  if (!value.startsWith('blob:')) {
    return
  }
  revokeObjectURL(value)
}

export function buildPdfReaderOpenState({
  doi = '',
  loadResult = null,
  previousBlobUrl = '',
  revokeObjectURL = URL.revokeObjectURL,
} = {}) {
  releasePdfBlobUrl(previousBlobUrl, revokeObjectURL)

  if (loadResult?.ok && loadResult?.blobUrl) {
    return {
      pdfUrl: String(loadResult.blobUrl),
      activeBlobUrl: String(loadResult.blobUrl),
      pdfError: null,
    }
  }

  const errorPayload = (loadResult?.errorPayload && typeof loadResult.errorPayload === 'object')
    ? loadResult.errorPayload
    : {}
  const quotaCard = buildQuotaErrorCardModel({
    code: errorPayload.code,
    message: errorPayload.message || errorPayload.error || '',
    data: errorPayload.data,
    featureTitle: '查看原文',
  })

  let message = 'PDF文件不存在'
  if (quotaCard) {
    message = quotaCard.rawMessage || quotaCard.headline
  } else if (isAuthFailure(errorPayload.code, Number(errorPayload.status || 0))) {
    message = '请先登录后使用'
  } else if (String(errorPayload.message || errorPayload.error || '').trim()) {
    message = String(errorPayload.message || errorPayload.error)
  }

  return {
    pdfUrl: '',
    activeBlobUrl: '',
    pdfError: {
      doi: String(doi || ''),
      message,
      quotaCard,
    },
  }
}
