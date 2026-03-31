// 工具函数

import { marked } from 'marked'

const DOI_INLINE_LINK_PATTERN = /\((?:doi\s*=|DOI:\s*)(10\.(?:[^\s,()]+|\([^\s,()]+\))+)(?:\s*·\s*查看原文[^)]*)?\)/gi
const DOI_LABELED_TEXT_PATTERN = /\[DOI:\s*[^\]\n]+\]|\((?:doi\s*=|DOI:\s*)[^)\n]+(?:\)[^)\n]*)?\)|\bdoi[:=]\s*10\.\d{4,9}[A-Za-z0-9._;()/:+-]*/gi
const DOI_PLAIN_TEXT_PATTERN = /\b(doi[:=]\s*)?(10\.[A-Za-z0-9._;()/:+-]+)/gi

function normalizeDoiForLink(raw) {
  return extractDoiLinks(raw)[0] || ''
}

function extractDoiLinks(raw) {
  let doi = String(raw || '').replace(/<[^>]*>/g, '').trim()
  if (!doi) return []

  doi = doi.replace(/^doi\s*[:=]\s*/i, '')
  doi = doi.replace(/·\s*查看原文.*/i, '')
  doi = doi.replace(/[)\],;:]+$/g, '')
  doi = doi.replace(/(10\.\d{1,9})(?=[A-Za-z])/gi, '$1/')

  const matches = doi.match(/10\.\d{1,9}[/_][A-Za-z0-9._;()/:-]+?(?=(?:10\.\d{1,9}[/_])|$)/gi)
  const candidates = matches && matches.length > 0 ? matches : [doi]
  const results = []
  const seen = new Set()

  for (const candidate of candidates) {
    let normalized = String(candidate || '').trim()
    if (normalized.includes('_') && !normalized.includes('/')) {
      normalized = normalized.replace('_', '/')
    }
    normalized = normalized.replace(/[)\],;:]+$/g, '')
    if (!/^10\.\d{1,9}\//i.test(normalized)) continue
    const key = normalized.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    results.push(normalized)
  }
  return results
}

function applyDoiLinksToHtml(html) {
  let nextHtml = String(html || '')
  nextHtml = nextHtml.replace(/\[DOI:\s*([^\]]+)\]/gi, (match, doi) => {
    const cleanDois = extractDoiLinks(doi)
    if (cleanDois.length === 0) return match
    return cleanDois.map((cleanDoi) => `<a href="#" class="doi-link" data-doi="${cleanDoi}">[DOI: ${cleanDoi}]</a>`).join(' ')
  })

  nextHtml = nextHtml.replace(DOI_INLINE_LINK_PATTERN, (match, doi) => {
    const cleanDois = extractDoiLinks(doi)
    if (cleanDois.length === 0) return match
    return cleanDois.map((cleanDoi) => `(<a href="#" class="doi-link" data-doi="${cleanDoi}">${cleanDoi}</a>)`).join(' ')
  })

  return applyPlainTextDoiLinksToHtml(nextHtml)
}

function applyPlainTextDoiLinksToHtml(html) {
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

      return segment.replace(DOI_PLAIN_TEXT_PATTERN, (match, prefix = '') => {
        const cleanDois = extractDoiLinks(match)
        if (cleanDois.length === 0) return match
        const rendered = cleanDois
          .map((cleanDoi) => `<a href="#" class="doi-link" data-doi="${cleanDoi}">${cleanDoi}</a>`)
          .join(' ')
        return prefix ? `${prefix}${rendered}` : rendered
      })
    })
    .join('')
}

function protectDoiSegments(text) {
  const placeholders = []
  const protectedText = String(text || '').replace(DOI_LABELED_TEXT_PATTERN, (match) => {
    if (!/10\.\d{1,9}/i.test(match)) return match
    const token = `@@DOI${placeholders.length}@@`
    placeholders.push(match)
    return token
  })

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

function containsMathMarkup(text) {
  const protectedText = protectDoiSegments(text).text
  return /\\\(|\\\[|\$\$?/.test(protectedText)
    || /[A-Za-z)\]](?:_\{[^{}\n]{1,32}\}|_[A-Za-z0-9+\-]{1,16}|\^\{[^{}\n]{1,32}\}|\^[A-Za-z0-9+\-]{1,16})/.test(protectedText)
}

function normalizeMarkdownForRender(text) {
  const input = String(text || '')
    .replace(/\r\n/g, '\n')
    .replace(/\u00a0/g, ' ')

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

  return applyDoiLinksToHtml(html)
}

export function formatStreamingAnswer(text) {
  if (!text) return ''

  const baseText = normalizeAnswerMarkdown(text, { renderMath: false })
  const shouldRenderMath = containsMathMarkup(baseText) || containsInlineRenderMarkup(baseText)

  if (!containsStructuredMarkdown(baseText) && !shouldRenderMath) {
    return applyDoiLinksToHtml(formatStreamingFallback(baseText))
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

  return applyDoiLinksToHtml(html)
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
  let next = doiProtection.text

  next = next.replace(/\\\[((?:.|\n)*?)\\\]/g, (_match, expr) => renderMathExpression(expr))
  next = next.replace(/\$\$([\s\S]*?)\$\$/g, (_match, expr) => renderMathExpression(expr))
  next = next.replace(/\\\(((?:.|\n)*?)\\\)/g, (_match, expr) => renderMathExpression(expr))
  next = next.replace(/\$([^$\n]+)\$/g, (_match, expr) => renderMathExpression(expr))

  return doiProtection.restore(renderSubSupMarkup(next))
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
