const DEFAULT_NEAR_BOTTOM_THRESHOLD_PX = 120

function normalizeNumber(value) {
  const number = Number(value)
  return Number.isFinite(number) ? number : 0
}

export function isNearBottom({
  scrollTop = 0,
  clientHeight = 0,
  scrollHeight = 0,
  thresholdPx = DEFAULT_NEAR_BOTTOM_THRESHOLD_PX,
} = {}) {
  const distanceToBottom = Math.max(0, normalizeNumber(scrollHeight) - normalizeNumber(scrollTop) - normalizeNumber(clientHeight))
  return distanceToBottom <= Math.max(0, normalizeNumber(thresholdPx))
}

export function shouldAutoScroll({ force = false, nearBottom = true } = {}) {
  return Boolean(force) || Boolean(nearBottom)
}

export { DEFAULT_NEAR_BOTTOM_THRESHOLD_PX }
