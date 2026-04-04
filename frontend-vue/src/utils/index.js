// 工具函数

import { marked } from 'marked'

function isDigit(char) {
  return char >= '0' && char <= '9'
}

function isAsciiLetter(char) {
  const code = char.charCodeAt(0)
  return (code >= 65 && code <= 90) || (code >= 97 && code <= 122)
}

function isDoiBoundary(char) {
  return !char || /\s|[>"'([{<]/.test(char)
}

function isDoiBodyChar(char) {
  return /[A-Za-z0-9._;/:+\-_()-]/.test(char)
}

function normalizeDoiForLink(raw) {
  let value = String(raw || '').trim()
  if (!value) return ''
  value = value.replace(/[)\],;:]+$/g, '')
  if (value.includes('_') && !value.includes('/')) {
    value = value.replace('_', '/')
  }
  return /^10\.\d{1,9}\//i.test(value) ? value : ''
}

function normalizePatentIdForLink(raw) {
  const value = String(raw || '').trim().replace(/[)\],;:]+$/g, '').toUpperCase()
  return /^[A-Z]{2}[A-Z0-9._/\-]+$/.test(value) ? value : ''
}

function readEnclosedSpan(text, startIndex, openChar, closeChar) {
  if (text[startIndex] !== openChar) return null
  let depth = 0
  for (let i = startIndex; i < text.length; i += 1) {
    const char = text[i]
    if (char === openChar) {
      depth += 1
      continue
    }
    if (char === closeChar) {
      depth -= 1
      if (depth === 0) {
        return {
          start: startIndex,
          end: i + 1,
          raw: text.slice(startIndex, i + 1),
          inner: text.slice(startIndex + 1, i),
        }
      }
    }
  }
  return null
}

function readDoiToken(text, startIndex) {
  if (!String(text || '').startsWith('10.', startIndex)) return null
  let i = startIndex + 3
  while (i < text.length && isDigit(text[i])) i += 1
  if (i === startIndex + 3) return null
  if (i >= text.length || !['/', '_'].includes(text[i])) return null
  i += 1

  let bodyStart = i
  let depth = 0
  while (i < text.length) {
    const char = text[i]
    if (!isDoiBodyChar(char)) break
    if (char === '(') depth += 1
    if (char === ')') {
      if (depth === 0) break
      depth -= 1
    }
    i += 1
  }

  if (i === bodyStart || depth !== 0) return null

  let end = i
  while (end > startIndex && /[.,;:]+/.test(text[end - 1])) {
    end -= 1
  }
  if (end <= startIndex) return null

  const normalized = normalizeDoiForLink(text.slice(startIndex, end))
  if (!normalized) return null

  return {
    start: startIndex,
    end,
    raw: text.slice(startIndex, end),
    normalized,
  }
}

function readDoiPrefixedSpan(text, startIndex) {
  const lower = text.slice(startIndex).toLowerCase()
  if (!lower.startsWith('doi')) return null
  const before = startIndex > 0 ? text[startIndex - 1] : ''
  if (before && /[A-Za-z0-9]/.test(before)) return null
  if (before === '(' || before === '[') return null

  let i = startIndex + 3
  while (i < text.length && /\s/.test(text[i])) i += 1
  if (![':', '='].includes(text[i])) return null
  i += 1
  while (i < text.length && /\s/.test(text[i])) i += 1

  const doiToken = readDoiToken(text, i)
  if (!doiToken) return null

  return {
    start: startIndex,
    end: doiToken.end,
    prefix: text.slice(startIndex, i),
    normalized: doiToken.normalized,
  }
}

function parseWrappedDoi(inner) {
  const trimmed = String(inner || '').trim()
  const prefixed = readDoiPrefixedSpan(trimmed, 0)
  if (!prefixed || prefixed.start !== 0) return null
  const suffix = trimmed.slice(prefixed.end).trim()
  if (suffix && !suffix.startsWith('·查看原文')) return null
  return prefixed.normalized
}

function readWrappedDoiSegment(text, startIndex) {
  const openChar = text[startIndex]
  const closeChar = openChar === '[' ? ']' : ')'
  const span = readEnclosedSpan(text, startIndex, openChar, closeChar)
  if (!span) return null
  const normalized = parseWrappedDoi(span.inner)
  if (!normalized) return null
  return {
    start: span.start,
    end: span.end,
    openChar,
    closeChar,
    normalized,
  }
}

function renderWrappedDoiLink(match) {
  return `${match.openChar}<a href="#" class="doi-link" data-doi="${match.normalized}">${match.normalized}</a>${match.closeChar}`
}

function readPlainDoiSegment(text, startIndex) {
  const current = text[startIndex]
  if (current !== '1' || text[startIndex + 1] !== '0' || text[startIndex + 2] !== '.') return null
  const before = startIndex > 0 ? text[startIndex - 1] : ''
  if (before && !isDoiBoundary(before) && before !== '\n') return null
  if (['=', ':'].includes(before)) return null
  return readDoiToken(text, startIndex)
}

function linkifyDoiTextSegment(text) {
  const source = String(text || '')
  let output = ''
  let i = 0

  while (i < source.length) {
    const wrapped = (source[i] === '(' || source[i] === '[')
      ? readWrappedDoiSegment(source, i)
      : null
    if (wrapped) {
      output += renderWrappedDoiLink(wrapped)
      i = wrapped.end
      continue
    }

    const prefixed = isAsciiLetter(source[i]) ? readDoiPrefixedSpan(source, i) : null
    if (prefixed) {
      output += `${prefixed.prefix}<a href="#" class="doi-link" data-doi="${prefixed.normalized}">${prefixed.normalized}</a>`
      i = prefixed.end
      continue
    }

    const plain = source[i] === '1' ? readPlainDoiSegment(source, i) : null
    if (plain) {
      output += `<a href="#" class="doi-link" data-doi="${plain.normalized}">${plain.normalized}</a>`
      i = plain.end
      continue
    }

    output += source[i]
    i += 1
  }

  return output
}

function applyDoiLinksToHtml(html) {
  const segments = String(html || '').split(/(<[^>]+>)/g)
  let inAnchor = false

  return segments
    .map((segment) => {
      if (!segment) return segment
      if (segment.startsWith('<')) {
        if (/^<a\b/i.test(segment)) {
          inAnchor = true
        } else if (/^<\/a\b/i.test(segment)) {
          inAnchor = false
        }
        return segment
      }
      if (inAnchor) return segment

      return linkifyDoiTextSegment(segment)
    })
    .join('')
}

function linkifyPatentTextSegment(text) {
  return String(text || '').replace(
    /\(\s*patent_id\s*=\s*([A-Za-z0-9._/\-]+)\s*\)/gi,
    (_match, rawPatentId) => {
      const patentId = normalizePatentIdForLink(rawPatentId)
      if (!patentId) return _match
      return `(<a href="#" class="doi-link patent-link" data-patent-id="${patentId}">patent_id=${patentId}</a>)`
    }
  )
}

function applyPatentLinksToHtml(html) {
  const segments = String(html || '').split(/(<[^>]+>)/g)
  let inAnchor = false

  return segments
    .map((segment) => {
      if (!segment) return segment
      if (segment.startsWith('<')) {
        if (/^<a\b/i.test(segment)) {
          inAnchor = true
        } else if (/^<\/a\b/i.test(segment)) {
          inAnchor = false
        }
        return segment
      }
      if (inAnchor) return segment
      return linkifyPatentTextSegment(segment)
    })
    .join('')
}

function applyCitationLinksToHtml(html) {
  return applyPatentLinksToHtml(applyDoiLinksToHtml(html))
}

function protectDoiSegments(text) {
  const placeholders = []
  const source = String(text || '')
  let protectedText = ''
  let i = 0

  while (i < source.length) {
    const wrapped = (source[i] === '(' || source[i] === '[')
      ? readWrappedDoiSegment(source, i)
      : null
    const prefixed = wrapped ? null : (isAsciiLetter(source[i]) ? readDoiPrefixedSpan(source, i) : null)
    const match = wrapped || prefixed

    if (match) {
      const raw = source.slice(match.start, match.end)
      const token = `@@DOI${placeholders.length}@@`
      placeholders.push(raw)
      protectedText += token
      i = match.end
      continue
    }

    protectedText += source[i]
    i += 1
  }

  return {
    text: protectedText,
    restore(value) {
      return placeholders.reduce(
        (result, original, index) => result.replaceAll(`@@DOI${index}@@`, original),
        String(value || '')
      )
    }
  }
}

function protectPatentSegments(text) {
  const placeholders = []
  const protectedText = String(text || '').replace(
    /\(\s*patent_id\s*=\s*[A-Za-z0-9._/\-]+\s*\)/gi,
    (raw) => {
      const token = `@@PATENT${placeholders.length}@@`
      placeholders.push(raw)
      return token
    }
  )

  return {
    text: protectedText,
    restore(value) {
      return placeholders.reduce(
        (result, original, index) => result.replaceAll(`@@PATENT${index}@@`, original),
        String(value || '')
      )
    }
  }
}

function containsMathMarkup(text) {
  const protectedText = protectDoiSegments(text).text
  return /\\\(|\\\[|\$\$?/.test(protectedText)
    || /[A-Za-z)\]](?:_\{[^{}\n]{1,32}\}|_[A-Za-z0-9+\-]{1,16}|\^\{[^{}\n]{1,32}\}|\^[A-Za-z0-9+\-]{1,16})/.test(protectedText)
}

function normalizeMarkdownForRender(text) {
  const input = String(text || '')
    .replace(/\r\n/g, '\n')
    .replace(/\u00a0/g, ' ')
    .replace(/([。！？：:])\s*(#{1,6}\s+)/g, '$1\n\n$2')

  const lines = input.split('\n')
  const normalized = []

  const isHeading = (line) => /^\s{0,3}#{1,6}\s+/.test(line)
  const isList = (line) => /^\s{0,3}(?:[-*+]\s+|\d+[.)]\s+)/.test(line)
  const isTable = (line) => line.includes('|') && !line.trim().startsWith('```')

  for (const rawLine of lines) {
    let line = String(rawLine || '').replace(/\t/g, '  ').replace(/[ \t]+$/g, '')
    const trimmed = line.trim()

    if (!trimmed) {
      if (normalized.length === 0 || normalized[normalized.length - 1] === '') continue
      normalized.push('')
      continue
    }

    line = line.replace(/^(\s{0,3}#{1,6})([^\s#])/, '$1 $2')
    line = line.replace(/^(\s{0,3}[-*+])([^\s])/, '$1 $2')
    line = line.replace(/^(\s{0,3}\d+[.)])([^\s])/, '$1 $2')

    const prev = normalized.length > 0 ? normalized[normalized.length - 1] : ''
    if (isHeading(line) && prev && prev.trim()) {
      normalized.push('')
    }
    if (isList(line) && prev && prev.trim() && !isList(prev)) {
      normalized.push('')
    }
    if (isTable(line) && prev && prev.trim() && !isTable(prev)) {
      normalized.push('')
    }

    normalized.push(line)
  }

  return normalized.join('\n').replace(/\n{3,}/g, '\n\n').trim()
}

function containsStructuredMarkdown(text) {
  return /(^|\n)\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|\|.+\|)/m.test(String(text || ''))
}

function containsInlineRenderMarkup(text) {
  return /<(?:sub|sup|span)\b/i.test(String(text || ''))
}

function looksLikeUnrenderedMarkdown(text, html) {
  if (!containsStructuredMarkdown(text)) return false
  if (/<(?:h[1-6]|ul|ol|li|table|blockquote)\b/i.test(String(html || ''))) return false
  return /(?:^|\n)\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+)/m.test(String(text || ''))
}

const BEIJING_TIME_ZONE = 'Asia/Shanghai'
const BEIJING_DATE_FORMATTER = new Intl.DateTimeFormat('zh-CN', {
  timeZone: BEIJING_TIME_ZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit'
})

function toValidDate(value) {
  const date = value instanceof Date ? value : new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

function formatBeijingDate(value) {
  const date = toValidDate(value)
  if (!date) return ''
  return BEIJING_DATE_FORMATTER.format(date).replace(/\//g, '-')
}

// 格式化时间
export function formatTime(date) {
  const d = toValidDate(date)
  if (!d) return ''

  const diff = Date.now() - d.getTime()

  if (diff < 60000) return '刚刚'
  if (diff < 3600000) return Math.floor(diff / 60000) + '分钟前'
  if (diff < 86400000) return Math.floor(diff / 3600000) + '小时前'
  return formatBeijingDate(d)
}

function renderMarkdownToHtml(text) {
  marked.setOptions({
    breaks: true,
    gfm: true,
    tables: true,
    mangle: false,
    headerIds: false
  })
  return marked.parse(text)
}

function formatStreamingFallback(text) {
  const escaped = escapeHtml(String(text))
  const normalized = escaped
    .replace(/\r\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')

  return normalized
    .replace(/^###\s+(.+)$/gm, '<h3>$1</h3>')
    .replace(/^##\s+(.+)$/gm, '<h2>$1</h2>')
    .replace(/^[-*+]\s+(.+)$/gm, '<div class="stream-bullet">• $1</div>')
    .replace(/^\d+[.)]\s+(.+)$/gm, '<div class="stream-bullet">$&</div>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\n/g, '<br>')
}

function normalizeAnswerMarkdown(text, options = {}) {
  const { renderMath = true } = options
  let normalizedText = normalizeMarkdownForRender(text)
  normalizedText = fixTableFormat(normalizedText)
  if (renderMath) {
    normalizedText = renderMathMarkup(normalizedText)
  }
  return normalizedText
}

// 格式化答案 - Markdown 渲染
export function formatAnswer(text, referenceSnippets = []) {
  if (!text) return ''
  void referenceSnippets

  const normalizedText = normalizeAnswerMarkdown(text)

  let html = ''
  try {
    html = renderMarkdownToHtml(normalizedText)
    if (looksLikeUnrenderedMarkdown(normalizedText, html)) {
      html = formatStreamingFallback(normalizedText)
    }
  } catch (e) {
    console.error('Markdown解析失败:', e)
    html = formatStreamingFallback(normalizedText)
  }

  return applyCitationLinksToHtml(html)
}

export function formatStreamingAnswer(text) {
  if (!text) return ''

  const baseText = normalizeAnswerMarkdown(text, { renderMath: false })
  const shouldRenderMath = containsMathMarkup(baseText) || containsInlineRenderMarkup(baseText)

  if (!containsStructuredMarkdown(baseText) && !shouldRenderMath) {
    return applyCitationLinksToHtml(formatStreamingFallback(baseText))
  }

  const normalizedText = shouldRenderMath ? renderMathMarkup(baseText) : baseText
  let html = ''

  try {
    html = renderMarkdownToHtml(normalizedText)
    if (looksLikeUnrenderedMarkdown(normalizedText, html)) {
      html = formatStreamingFallback(normalizedText)
    }
  } catch (e) {
    console.error('流式Markdown解析失败:', e)
    html = formatStreamingFallback(normalizedText)
  }

  return applyCitationLinksToHtml(html)
}

// 修复表格格式
function fixTableFormat(text) {
  const lines = text.split('\n')
  const result = []
  let i = 0
  
  while (i < lines.length) {
    const line = lines[i]
    
    if (line.includes('|') && !line.trim().startsWith('```')) {
      const tableLines = []
      let j = i
      while (j < lines.length && lines[j].includes('|')) {
        tableLines.push(lines[j])
        j++
      }
      
      if (tableLines.length >= 2) {
        const hasSeparator = tableLines[1].match(/^\s*\|[\s\-:|]+\|\s*$/)
        
        if (!hasSeparator) {
          const headerCols = (tableLines[0].match(/\|/g) || []).length - 1
          const separator = '|' + Array(headerCols).fill('------').join('|') + '|'
          tableLines.splice(1, 0, separator)
        }
        
        result.push(...tableLines)
        i = j
        continue
      }
    }
    
    result.push(line)
    i++
  }
  
  return result.join('\n')
}

function renderMathMarkup(text) {
  const doiProtection = protectDoiSegments(text)
  const patentProtection = protectPatentSegments(doiProtection.text)
  let next = patentProtection.text

  next = next.replace(/\\\[((?:.|\n)*?)\\\]/g, (_match, expr) => renderMathExpression(expr))
  next = next.replace(/\$\$([\s\S]*?)\$\$/g, (_match, expr) => renderMathExpression(expr))
  next = next.replace(/\\\(((?:.|\n)*?)\\\)/g, (_match, expr) => renderMathExpression(expr))
  next = next.replace(/\$([^$\n]+)\$/g, (_match, expr) => renderMathExpression(expr))

  return doiProtection.restore(patentProtection.restore(renderSubSupMarkup(next)))
}

function renderMathExpression(text) {
  let expr = normalizeMathCommands(String(text || '').trim())
  expr = renderFractions(expr)
  expr = renderSubSupMarkup(expr)
  return expr
}

function normalizeMathCommands(text) {
  const replacements = new Map([
    ['\\rightarrow', '→'],
    ['\\leftarrow', '←'],
    ['\\Rightarrow', '⇒'],
    ['\\Leftarrow', '⇐'],
    ['\\geq', '≥'],
    ['\\leq', '≤'],
    ['\\times', '×'],
    ['\\cdot', '·'],
    ['\\pm', '±'],
    ['\\alpha', 'α'],
    ['\\beta', 'β'],
    ['\\gamma', 'γ'],
    ['\\delta', 'δ'],
    ['\\lambda', 'λ'],
    ['\\mu', 'μ'],
    ['\\sigma', 'σ'],
    ['\\Delta', 'Δ'],
  ])

  let next = String(text || '')
  for (const [source, target] of replacements.entries()) {
    next = next.replaceAll(source, target)
  }
  next = next.replace(/\\text\{([^{}]+)\}/g, '$1')
  next = next.replace(/\\mathrm\{([^{}]+)\}/g, '$1')
  next = next.replace(/\\operatorname\{([^{}]+)\}/g, '$1')
  next = next.replace(/\\([{}])/g, '$1')
  next = next.replace(/\\[a-zA-Z]+/g, '')
  return next
}

function renderFractions(text) {
  return String(text || '').replace(
    /\\frac\{([^{}]+)\}\{([^{}]+)\}/g,
    '<span class="math-frac"><span class="math-frac-num">$1</span><span class="math-frac-den">$2</span></span>'
  )
}

function renderSubSupMarkup(text) {
  return String(text || '')
    .replace(/([A-Za-z0-9)\]])_\{([^{}\n]+)\}/g, '$1<sub>$2</sub>')
    .replace(/([A-Za-z0-9)\]])_([A-Za-z0-9+\-]+)/g, '$1<sub>$2</sub>')
    .replace(/([A-Za-z0-9)\]])\^\{([^{}\n]+)\}/g, '$1<sup>$2</sup>')
    .replace(/([A-Za-z0-9)\]])\^([A-Za-z0-9+\-]+)/g, '$1<sup>$2</sup>')
    .replace(/<\/sub><sub>/g, '')
    .replace(/<\/sup><sup>/g, '')
}

// HTML 转义
export function escapeHtml(text) {
  const div = document.createElement('div')
  div.textContent = text
  return div.innerHTML
}
