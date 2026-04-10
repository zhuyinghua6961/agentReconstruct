function normalizeTerminalEvent(message = {}) {
  const metadata = message?.metadata && typeof message.metadata === 'object' ? message.metadata : {}
  const doneSeen = message?.doneSeen ?? message?.done_seen ?? metadata.done_seen
  if (doneSeen === true) return 'done'

  const rawEvent = String(
    message?.terminalStatus
    ?? message?.terminal_status
    ?? message?.status
    ?? metadata.streaming_terminal_event
    ?? metadata.terminal_status
    ?? metadata.status
    ?? ''
  ).trim().toLowerCase()

  if (rawEvent === 'completed') return 'done'
  if (rawEvent === 'cancelled') return 'canceled'
  if (rawEvent) return rawEvent
  return ''
}

export function shouldIgnoreLateStreamContent(message = {}) {
  return normalizeTerminalEvent(message) === 'done'
}

export function shouldIgnoreLateStreamError(message = {}) {
  return normalizeTerminalEvent(message) === 'done'
}
