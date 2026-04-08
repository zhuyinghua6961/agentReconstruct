export async function focusQuestionItem({
  item,
  userMessageElements,
  revealHiddenHistory,
  nextTick,
  setActiveQuestionMessageIndex,
  setHighlightedQuestionMessageIndex,
  scheduleHighlightReset,
  behavior = 'smooth',
  highlight = true,
} = {}) {
  const messageIndex = Number(item?.messageIndex)
  if (!Number.isInteger(messageIndex) || messageIndex < 0) return false

  if (typeof setActiveQuestionMessageIndex === 'function') {
    setActiveQuestionMessageIndex(messageIndex)
  }

  const reveal = typeof revealHiddenHistory === 'function' ? revealHiddenHistory : () => false
  const tick = typeof nextTick === 'function' ? nextTick : async () => {}
  const didReveal = Boolean(reveal(messageIndex))
  if (didReveal) {
    await tick()
  }
  await tick()

  const target = userMessageElements instanceof Map
    ? userMessageElements.get(messageIndex)
    : null
  if (!target || typeof target.scrollIntoView !== 'function') {
    return false
  }

  target.scrollIntoView({ behavior, block: 'start' })
  if (!highlight) {
    return true
  }

  if (typeof setHighlightedQuestionMessageIndex === 'function') {
    setHighlightedQuestionMessageIndex(messageIndex)
  }
  if (typeof scheduleHighlightReset === 'function') {
    scheduleHighlightReset()
  }
  return true
}
