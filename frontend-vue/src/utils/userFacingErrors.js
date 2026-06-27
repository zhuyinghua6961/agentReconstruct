const CODE_MESSAGES = {
  UPSTREAM_STREAM_UNAVAILABLE: '上游流式服务暂时不可用，请稍后重试',
  UPSTREAM_ERROR: '上游模型服务异常，请稍后重试',
  UPSTREAM_TIMEOUT: '模型响应超时，请稍后重试',
  UPSTREAM_POOL_TIMEOUT: '模型连接繁忙，请稍后重试',
  LLM_UNAVAILABLE: 'LLM 服务不可用',
  EMBEDDING_UNAVAILABLE: 'Embedding 模型不可用',
  RETRIEVAL_FAILED: '文献检索失败',
  UPSTREAM_STREAM_INTERRUPTED: '模型流式输出中断',
  RERANK_DEGRADED: '重排序服务不可用，已按向量相似度排序继续',
  STAGE1_JSON_INVALID: '大模型输出 json 不规范，请重试',
  STAGE1_NO_RETRIEVAL_CLAIMS: '大模型未输出检索词，请重试',
  STAGE2_NO_DOI: 'metadata 无 doi，请重试',
  ASK_CANCELLED: '已取消生成',
  TASK_EXPIRED: '这次回答已过期，请重新提问',
  INTERNAL_ERROR: '服务器内部错误',
  FASTQA_NOT_READY: '快速问答服务未就绪',
  FASTQA_RUNTIME_ERROR: '快速问答执行异常，请稍后重试',
  FASTQA_AUTHORITY_PREFLIGHT_FAILED: '快速问答权限预检失败',
  EMBEDDING_UNAVAILABLE: '语义检索依赖的向量服务不可用',
  RETRIEVAL_RUNTIME_UNAVAILABLE: '检索服务暂不可用',
  PATENT_FILE_ROUTE_DISABLED: '专利文件问答功能已禁用',
  CONVERSATION_FILE_PROVIDER_UNAVAILABLE: '会话文件服务不可用',
  FILE_SELECTION_CLARIFICATION_REQUIRED: '文件选择需要澄清',
  FILE_NOT_READY: '文件处理中，请等待就绪后重试',
  FILE_PROCESSING_FAILED: '文件处理失败，请重新上传',
  FILE_NOT_FOUND: '文件不存在或已失效',
  EXECUTION_FILE_UNAVAILABLE: '暂时无法读取所选文件',
  FILE_STORAGE_REF_MISSING: '文件缺少可执行的存储引用',
  FILE_STORAGE_REF_NOT_MINIO: '文件存储格式当前不可用于问答',
  QUOTA_PRECHECK_FAILED: '配额预检失败',
  PDF_ANSWER_BACKEND_UNAVAILABLE: 'PDF 作答后端不可用',
  PDF_QA_FAILED: 'PDF 问答失败',
  MULTI_PDF_BACKEND_UNAVAILABLE: '多 PDF 后端不可用',
  ASK_STREAM_BUSY: '当前问答请求过多，请稍后重试',
}

const ERROR_NAME_TO_CODE = {
  upstream_stream_unavailable: 'UPSTREAM_STREAM_UNAVAILABLE',
  upstream_error: 'UPSTREAM_ERROR',
  upstream_pool_timeout: 'UPSTREAM_POOL_TIMEOUT',
  execution_file_unavailable: 'EXECUTION_FILE_UNAVAILABLE',
  file_not_ready: 'FILE_NOT_READY',
  file_processing_failed: 'FILE_PROCESSING_FAILED',
  file_not_found: 'FILE_NOT_FOUND',
  storage_ref_missing: 'FILE_STORAGE_REF_MISSING',
  storage_ref_not_minio: 'FILE_STORAGE_REF_NOT_MINIO',
  pdf_answer_backend_unavailable: 'PDF_ANSWER_BACKEND_UNAVAILABLE',
  pdf_qa_failed: 'PDF_QA_FAILED',
  multi_pdf_backend_unavailable: 'MULTI_PDF_BACKEND_UNAVAILABLE',
  cancelled: 'ASK_CANCELLED',
  internal_error: 'INTERNAL_ERROR',
  timeout: 'UPSTREAM_TIMEOUT',
  llm_unavailable: 'LLM_UNAVAILABLE',
  embedding_unavailable: 'EMBEDDING_UNAVAILABLE',
  retrieval_failed: 'RETRIEVAL_FAILED',
  upstream_stream_interrupted: 'UPSTREAM_STREAM_INTERRUPTED',
  rerank_degraded: 'RERANK_DEGRADED',
  stage1_json_invalid: 'STAGE1_JSON_INVALID',
  stage1_no_retrieval_claims: 'STAGE1_NO_RETRIEVAL_CLAIMS',
  stage2_no_doi: 'STAGE2_NO_DOI',
}

const TECHNICAL_PATTERNS = [
  { pattern: /upstream_pool_timeout/i, message: CODE_MESSAGES.UPSTREAM_POOL_TIMEOUT },
  { pattern: /upstream_stream_unavailable/i, message: CODE_MESSAGES.UPSTREAM_STREAM_UNAVAILABLE },
  { pattern: /upstream_error/i, message: CODE_MESSAGES.UPSTREAM_ERROR },
  { pattern: /upstream model timeout/i, message: CODE_MESSAGES.UPSTREAM_TIMEOUT },
  { pattern: /uploaded file is not ready for direct reading yet/i, message: CODE_MESSAGES.FILE_NOT_READY },
  { pattern: /local pdf paths are disabled/i, message: '文件问答已禁用本地 PDF 路径，请使用 MinIO 存储的文件重试' },
  { pattern: /pdf branch selected but no readable pdf source/i, message: '已选择 PDF 分支，但没有可读的 PDF 来源' },
  { pattern: /pdf_content_unavailable/i, message: 'PDF 内容不可用' },
  { pattern: /patent execution timed out/i, message: '专利问答执行超时' },
  { pattern: /patent file routes are disabled/i, message: CODE_MESSAGES.PATENT_FILE_ROUTE_DISABLED },
  { pattern: /patent ask service is not ready/i, message: '专利问答服务未就绪' },
  { pattern: /fastqa generation runtime is not ready/i, message: CODE_MESSAGES.FASTQA_NOT_READY },
  { pattern: /too many running requests/i, message: '当前并发请求过多，请稍后重试' },
  { pattern: /too many running patent streams/i, message: '当前专利流式请求过多，请稍后重试' },
  { pattern: /read timed out|readtimeout/i, message: CODE_MESSAGES.UPSTREAM_TIMEOUT },
  { pattern: /connect timeout|connection timed out/i, message: CODE_MESSAGES.UPSTREAM_TIMEOUT },
  { pattern: /connection refused|failed to establish a new connection/i, message: '无法连接上游服务，请稍后重试' },
  { pattern: /pool timeout|pooltimeout/i, message: CODE_MESSAGES.UPSTREAM_POOL_TIMEOUT },
  { pattern: /internal server error/i, message: CODE_MESSAGES.INTERNAL_ERROR },
  { pattern: /empty execution result/i, message: '执行未产生有效结果' },
  { pattern: /cancelled/i, message: CODE_MESSAGES.ASK_CANCELLED },
]

const HTTP_STATUS_MESSAGES = {
  400: '请求参数无效',
  401: '未登录或登录已过期',
  403: '没有权限执行此操作',
  404: '请求的资源不存在',
  408: '请求超时',
  429: '请求过于频繁，请稍后重试',
  500: '服务器内部错误',
  502: '网关错误，请稍后重试',
  503: '服务暂时不可用，请稍后重试',
  504: '网关超时，请稍后重试',
}

function normalizeCode(code = '', error = '') {
  const normalizedCode = String(code || '').trim().toUpperCase()
  if (normalizedCode) return normalizedCode
  const errorName = String(error || '').trim().toLowerCase()
  return ERROR_NAME_TO_CODE[errorName] || String(errorName || '').trim().toUpperCase()
}

function looksChinese(text) {
  return /[\u3400-\u9fff]/.test(String(text || ''))
}

function isMachineMessage(message, error = '') {
  const raw = String(message || '').trim()
  if (!raw) return true
  const errorName = String(error || '').trim().toLowerCase()
  if (errorName && raw.toLowerCase() === errorName) return true
  if (/^[a-z0-9_:-]+$/i.test(raw) && !looksChinese(raw)) return true
  return false
}

export function humanizeTechnicalMessage(raw = '', { code = '', error = '' } = {}) {
  const text = String(raw || '').trim()
  const normalizedCode = normalizeCode(code, error)
  if (CODE_MESSAGES[normalizedCode]) {
    if (!text || isMachineMessage(text, error)) {
      return CODE_MESSAGES[normalizedCode]
    }
  }
  if (!text) {
    return CODE_MESSAGES[normalizedCode] || '处理失败，请稍后重试'
  }
  if (looksChinese(text) && !isMachineMessage(text, error)) {
    return text
  }
  for (const item of TECHNICAL_PATTERNS) {
    if (item.pattern.test(text)) {
      return item.message
    }
  }
  if (CODE_MESSAGES[normalizedCode]) {
    return CODE_MESSAGES[normalizedCode]
  }
  return '处理失败，请稍后重试'
}

export function formatUserFacingError({ code = '', error = '', message = '', metadata = {} } = {}) {
  const mergedCode = normalizeCode(code || metadata?.error_code || '', error || metadata?.error_name || '')
  const mergedMessage = String(message || metadata?.error_message || '').trim()
  const mergedError = String(error || metadata?.error_name || '').trim()
  const statusCode = Number(metadata?.status_code ?? metadata?.http_status ?? 0) || null
  let resolved = humanizeTechnicalMessage(mergedMessage || mergedError, {
    code: mergedCode,
    error: mergedError,
  })
  if (statusCode && !/\bHTTP\s+\d{3}\b/i.test(resolved)) {
    resolved = `${resolved}（HTTP ${statusCode}）`
  }
  return resolved
}

export function formatHttpError(status, path = '') {
  const code = Number(status || 0)
  const base = HTTP_STATUS_MESSAGES[code] || `请求失败（${code || '未知'}）`
  const cleanPath = String(path || '').trim()
  if (!cleanPath) return base
  return `${base}`
}
