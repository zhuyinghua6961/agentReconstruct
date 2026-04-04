export function normalizeClipboardText(rawText) {
  return String(rawText || '').trim()
}

export function buildTranslatePayload(text) {
  return [text]
}

export function classifyClipboardFailure(error, runtimeContext) {
  if (
    !runtimeContext?.hasNavigator ||
    !runtimeContext?.hasClipboardApi ||
    !runtimeContext?.hasReadText ||
    !runtimeContext?.isSecureContext
  ) {
    return 'unsupported'
  }

  const errorName = String(error?.name || '').trim()
  if (errorName === 'NotAllowedError' || errorName === 'SecurityError') {
    return 'denied'
  }

  return 'unknown'
}

export function getClipboardFeedbackMessage(kind) {
  if (kind === 'empty') return '剪贴板里没有可翻译的文本'
  if (kind === 'unsupported') return '当前环境不支持一键读取剪贴板，请手动粘贴'
  if (kind === 'denied') return '浏览器不允许直接读取剪贴板，请手动粘贴'
  return '读取剪贴板失败，请手动粘贴后再试'
}
