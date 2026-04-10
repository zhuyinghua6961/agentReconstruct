import { formatAnswer, formatStreamingAnswer } from './index.js'

const DEFAULT_MIN_INTERVAL_MS = 24
const DEFAULT_MAX_INTERVAL_MS = 120
const DEFAULT_DURATION_MULTIPLIER = 6

export function createStreamingHtmlRenderer(options = {}) {
  const formatter = typeof options.formatter === 'function' ? options.formatter : formatStreamingAnswer
  const terminalFormatter = typeof options.terminalFormatter === 'function'
    ? options.terminalFormatter
    : ((text, message) => formatAnswer(text, Array.isArray(message?.referenceLinks) ? message.referenceLinks : []))
  const now = typeof options.now === 'function' ? options.now : () => Date.now()
  const measureNow = typeof options.measureNow === 'function'
    ? options.measureNow
    : (() => {
        if (typeof globalThis?.performance?.now === 'function') {
          return globalThis.performance.now()
        }
        return now()
      })
  const hasExplicitMinInterval = Object.prototype.hasOwnProperty.call(options, 'minIntervalMs')
  const minIntervalMs = Math.max(
    0,
    Number(hasExplicitMinInterval ? options.minIntervalMs : DEFAULT_MIN_INTERVAL_MS) || 0,
  )
  const maxIntervalMs = Math.max(
    minIntervalMs,
    Number(
      Object.prototype.hasOwnProperty.call(options, 'maxIntervalMs')
        ? options.maxIntervalMs
        : (hasExplicitMinInterval ? minIntervalMs : DEFAULT_MAX_INTERVAL_MS),
    ) || minIntervalMs,
  )
  const durationMultiplier = Math.max(1, Number(options.durationMultiplier || DEFAULT_DURATION_MULTIPLIER))
  const cache = new WeakMap()

  return function renderStreamingHtml(message) {
    if (!message || typeof message !== 'object') return formatter('')
    const content = String(message?.content || '')
    const metadata = message?.metadata && typeof message.metadata === 'object' ? message.metadata : {}
    const referenceLinks = Array.isArray(message?.referenceLinks) ? message.referenceLinks : null
    const doneSeen = Boolean(message?.doneSeen ?? message?.done_seen ?? metadata?.done_seen)
    const terminalStatus = String(
      message?.terminalStatus
      ?? message?.terminal_status
      ?? message?.status
      ?? metadata?.terminal_status
      ?? metadata?.status
      ?? metadata?.streaming_terminal_event
      ?? ''
    ).trim().toLowerCase()
    const isTerminal = message?.isComplete === true
      || doneSeen
      || ['done', 'completed', 'failed', 'canceled', 'cancelled', 'expired', 'error'].includes(terminalStatus)
    const existing = cache.get(message)

    if (
      existing
      && existing.content === content
      && existing.isTerminal === isTerminal
      && existing.referenceLinks === referenceLinks
    ) {
      return existing.html
    }

    if (
      !isTerminal
      && existing
      && existing.isTerminal !== true
      && existing.referenceLinks === referenceLinks
      && content.startsWith(existing.content)
      && now() < existing.nextRenderAt
    ) {
      return existing.html
    }

    const renderStartedAt = measureNow()
    const html = isTerminal ? terminalFormatter(content, message) : formatter(content, message)
    const renderDurationMs = Math.max(0, Number(measureNow() - renderStartedAt) || 0)
    const nextIntervalMs = Math.min(
      maxIntervalMs,
      Math.max(minIntervalMs, Math.ceil(renderDurationMs * durationMultiplier)),
    )
    const renderedAt = now()
    cache.set(message, {
      content,
      html,
      renderedAt,
      renderDurationMs,
      nextRenderAt: renderedAt + nextIntervalMs,
      isTerminal,
      referenceLinks,
    })
    return html
  }
}

export { DEFAULT_MIN_INTERVAL_MS, DEFAULT_MAX_INTERVAL_MS }
