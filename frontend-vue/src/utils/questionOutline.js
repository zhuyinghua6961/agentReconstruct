function normalizeContent(content) {
  return String(content || '').replace(/\s+/g, ' ').trim()
}

export function getQuestionAnchorId(messageIndex) {
  return `question-${messageIndex}`
}

export function getQuestionPreview(content) {
  const normalized = normalizeContent(content)
  if (!normalized) return '未命名问题'
  return normalized.length > 48 ? `${normalized.slice(0, 48)}...` : normalized
}

export function buildQuestionOutlineSignature(messages = []) {
  if (!Array.isArray(messages) || messages.length === 0) return ''

  return messages.reduce((parts, message, messageIndex) => {
    if (String(message?.role || '').trim().toLowerCase() !== 'user') return parts
    parts.push(`${messageIndex}:${getQuestionPreview(message?.content || '')}`)
    return parts
  }, []).join('\u0001')
}

export function buildQuestionOutlineItems(messages = []) {
  if (!Array.isArray(messages) || messages.length === 0) return []

  let outlineIndex = 0
  return messages.reduce((items, message, messageIndex) => {
    if (String(message?.role || '').trim().toLowerCase() !== 'user') return items
    outlineIndex += 1
    items.push({
      outlineIndex,
      messageIndex,
      anchorId: getQuestionAnchorId(messageIndex),
      preview: getQuestionPreview(message?.content || ''),
    })
    return items
  }, [])
}
