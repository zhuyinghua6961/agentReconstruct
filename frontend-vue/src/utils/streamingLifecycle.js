function normalizeTerminalEvent(message = {}) {
  const metadata = message?.metadata && typeof message.metadata === 'object' ? message.metadata : {}
  const rawEvent = String(metadata.streaming_terminal_event || '').trim().toLowerCase()
  if (rawEvent) return rawEvent
  if (metadata.done_seen === true) return 'done'
  return ''
}

export function shouldIgnoreLateStreamError(message = {}) {
  return normalizeTerminalEvent(message) === 'done'
}
