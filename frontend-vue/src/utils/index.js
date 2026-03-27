// 工具函数

import { marked } from 'marked'

function normalizeDoiForLink(raw) {
  let doi = String(raw || '').replace(/<[^>]*>/g, '').trim()
  if (!doi) return ''

  doi = doi.replace(/^doi\s*=\s*/i, '')
  doi = doi.replace(/·\s*查看原文.*/i, '')
  doi = doi.replace(/[)\],;:]+$/g, '')

  const m = doi.match(/10\.[^\s;)\]·]+/i)
  if (m) doi = m[0]

  if (doi.includes('_') && !doi.includes('/')) {
    doi = doi.replace('_', '/')
  }
  return doi
}

function applyDoiLinksToHtml(html) {
  let nextHtml = String(html || '')
  nextHtml = nextHtml.replace(/\[DOI:\s*([^\]]+)\]/gi, (match, doi) => {
    const cleanDoi = normalizeDoiForLink(doi)
    if (!cleanDoi) return match
    return `<a href="#" class="doi-link" data-doi="${cleanDoi}">[DOI: ${cleanDoi}]</a>`
  })

  nextHtml = nextHtml.replace(/\(doi\s*=\s*([^)\s]+(?:\s*·\s*查看原文[^)]*)?)\)/gi, (match, doi) => {
    const cleanDoi = normalizeDoiForLink(doi)
    if (!cleanDoi) return match
    return `(<a href="#" class="doi-link" data-doi="${cleanDoi}">${cleanDoi}</a>)`
  })

  return nextHtml
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

function normalizeAnswerMarkdown(text) {
  let normalizedText = normalizeMarkdownForRender(text)
  normalizedText = fixTableFormat(normalizedText)
  normalizedText = renderMathMarkup(normalizedText)
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

  const normalizedText = normalizeAnswerMarkdown(text)
  let html = ''

  if (!containsStructuredMarkdown(normalizedText) && !containsInlineRenderMarkup(normalizedText)) {
    html = formatStreamingFallback(normalizedText)
    return applyDoiLinksToHtml(html)
  }

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
  let next = String(text || '')

  next = next.replace(/\\\[((?:.|\n)*?)\\\]/g, (_match, expr) => renderMathExpression(expr))
  next = next.replace(/\$\$([\s\S]*?)\$\$/g, (_match, expr) => renderMathExpression(expr))
  next = next.replace(/\\\(((?:.|\n)*?)\\\)/g, (_match, expr) => renderMathExpression(expr))
  next = next.replace(/\$([^$\n]+)\$/g, (_match, expr) => renderMathExpression(expr))

  return renderSubSupMarkup(next)
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
