function isAssistantLike(message) {
  const role = String(message?.role || '').trim().toLowerCase()
  return role === 'assistant' || role === 'bot'
}

function normalizeRequestId(requestId) {
  return String(requestId || '').trim()
}

function normalizeIndex(index) {
  return Number.isInteger(index) ? index : -1
}

export function resolveStreamingTarget({ messages, requestId = '', cachedTargetIndex = -1 } = {}) {
  if (!Array.isArray(messages) || messages.length === 0) return null

  const normalizedRequestId = normalizeRequestId(requestId)
  const normalizedCachedIndex = normalizeIndex(cachedTargetIndex)

  if (normalizedCachedIndex >= 0 && normalizedCachedIndex < messages.length) {
    const cachedMessage = messages[normalizedCachedIndex]
    if (isAssistantLike(cachedMessage) && (!normalizedRequestId || cachedMessage?.streamRequestId === normalizedRequestId)) {
      return {
        index: normalizedCachedIndex,
        message: cachedMessage,
        resolvedBy: 'cached_index',
      }
    }
  }

  if (normalizedRequestId) {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const message = messages[index]
      if (isAssistantLike(message) && message?.streamRequestId === normalizedRequestId) {
        return {
          index,
          message,
          resolvedBy: 'request_id_scan',
        }
      }
    }
  }

  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index]
    if (isAssistantLike(message)) {
      return {
        index,
        message,
        resolvedBy: 'last_assistant_scan',
      }
    }
  }

  return null
}
