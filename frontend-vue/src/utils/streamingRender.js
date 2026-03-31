import { formatStreamingAnswer } from './index.js'

const DEFAULT_MIN_INTERVAL_MS = 120

export function createStreamingHtmlRenderer(options = {}) {
  const formatter = typeof options.formatter === 'function' ? options.formatter : formatStreamingAnswer
  const now = typeof options.now === 'function' ? options.now : () => Date.now()
  const minIntervalMs = Math.max(0, Number(options.minIntervalMs || DEFAULT_MIN_INTERVAL_MS))
  const cache = new WeakMap()

  return function renderStreamingHtml(message) {
    if (!message || typeof message !== 'object') return formatter('')
    const content = String(message?.content || '')
    const existing = cache.get(message)

    if (existing && existing.content === content) {
      return existing.html
    }

    if (existing && content.startsWith(existing.content) && (now() - existing.renderedAt) < minIntervalMs) {
      return existing.html
    }

    const html = formatter(content)
    cache.set(message, {
      content,
      html,
      renderedAt: now(),
    })
    return html
  }
}

export { DEFAULT_MIN_INTERVAL_MS }
