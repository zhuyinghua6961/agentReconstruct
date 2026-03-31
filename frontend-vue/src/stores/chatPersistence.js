const RUNTIME_ONLY_MESSAGE_FIELDS = ['streamRequestId']

function cloneValue(value) {
  if (value instanceof Date) {
    return value.toISOString()
  }
  if (Array.isArray(value)) {
    return value.map((item) => cloneValue(item))
  }
  if (value && typeof value === 'object') {
    return Object.entries(value).reduce((acc, [key, item]) => {
      acc[key] = cloneValue(item)
      return acc
    }, {})
  }
  return value
}

function sanitizeMessage(message = {}) {
  const sanitized = cloneValue(message)
  for (const field of RUNTIME_ONLY_MESSAGE_FIELDS) {
    delete sanitized[field]
  }
  return sanitized
}

function sanitizeChat(chat = {}) {
  const sanitized = cloneValue(chat)
  sanitized.messages = Array.isArray(chat?.messages) ? chat.messages.map((message) => sanitizeMessage(message)) : []
  return sanitized
}

export function restorePersistedChats(rawChats = []) {
  if (!Array.isArray(rawChats)) return []
  return rawChats.map((chat) => sanitizeChat(chat))
}

export function prepareChatsForPersistence(rawChats = []) {
  if (!Array.isArray(rawChats)) return []
  return rawChats.map((chat) => sanitizeChat(chat))
}

export { RUNTIME_ONLY_MESSAGE_FIELDS }
