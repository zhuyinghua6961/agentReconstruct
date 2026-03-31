export const STREAM_PERSIST_DEBOUNCE_MS = 1200

export function resolveChatPersistPolicy({ force = false, isStreaming = false } = {}) {
  if (force || !isStreaming) {
    return { mode: 'immediate', debounceMs: 0 }
  }
  return { mode: 'debounced', debounceMs: STREAM_PERSIST_DEBOUNCE_MS }
}

export function shouldForcePersistForStreamingTransition({
  previousIsStreaming = false,
  nextIsStreaming = false,
} = {}) {
  return Boolean(previousIsStreaming) && !Boolean(nextIsStreaming)
}
