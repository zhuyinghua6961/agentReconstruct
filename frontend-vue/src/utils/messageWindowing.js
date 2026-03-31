function normalizeNonNegativeInteger(value, fallback = 0) {
  const number = Number(value)
  if (!Number.isInteger(number) || number < 0) return fallback
  return number
}

export function buildVisibleMessageWindow({
  messages = [],
  visibleCount = 0,
  revealedCount = 0,
} = {}) {
  const safeMessages = Array.isArray(messages) ? messages : []
  const totalMessages = safeMessages.length
  const normalizedVisibleCount = normalizeNonNegativeInteger(visibleCount, totalMessages)
  const normalizedRevealedCount = normalizeNonNegativeInteger(revealedCount, 0)
  const visibleSpan = Math.min(totalMessages, normalizedVisibleCount + normalizedRevealedCount)
  const visibleStartIndex = Math.max(0, totalMessages - visibleSpan)
  const visibleMessages = safeMessages.slice(visibleStartIndex).map((message, offset) => ({
    absoluteMessageIndex: visibleStartIndex + offset,
    message,
  }))

  return {
    totalMessages,
    hiddenCount: visibleStartIndex,
    visibleMessages,
  }
}

export function resolveHiddenHistoryReveal({
  totalMessages = 0,
  visibleCount = 0,
  revealedCount = 0,
  batchSize = 0,
  targetAbsoluteIndex = -1,
} = {}) {
  const normalizedTotalMessages = normalizeNonNegativeInteger(totalMessages, 0)
  const normalizedVisibleCount = normalizeNonNegativeInteger(visibleCount, normalizedTotalMessages)
  const normalizedRevealedCount = normalizeNonNegativeInteger(revealedCount, 0)
  const normalizedBatchSize = Math.max(1, normalizeNonNegativeInteger(batchSize, 1))
  const normalizedTargetIndex = normalizeNonNegativeInteger(targetAbsoluteIndex, -1)

  const currentlyVisibleStart = Math.max(0, normalizedTotalMessages - normalizedVisibleCount - normalizedRevealedCount)
  if (normalizedTargetIndex >= currentlyVisibleStart) {
    return {
      needsReveal: false,
      nextRevealedCount: normalizedRevealedCount,
    }
  }

  const minimumRevealNeeded = Math.max(
    0,
    normalizedTotalMessages - normalizedVisibleCount - normalizedTargetIndex
  )
  const nextRevealedCount = Math.min(
    Math.max(normalizedRevealedCount + normalizedBatchSize, minimumRevealNeeded),
    Math.max(0, normalizedTotalMessages - normalizedVisibleCount)
  )

  return {
    needsReveal: true,
    nextRevealedCount,
  }
}
