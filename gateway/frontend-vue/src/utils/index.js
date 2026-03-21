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

// 格式化答案 - Markdown 渲染
export function formatAnswer(text, referenceSnippets = []) {
  if (!text) return ''
  void referenceSnippets

  let normalizedText = normalizeMarkdownForRender(text)
  normalizedText = fixTableFormat(normalizedText)
  normalizedText = cleanLaTeX(normalizedText)
  
  marked.setOptions({
    breaks: true,
    gfm: true,
    tables: true,
    mangle: false,
    headerIds: false
  })
  
  let html = ''
  try {
    html = marked.parse(normalizedText)
    if (looksLikeUnrenderedMarkdown(normalizedText, html)) {
      html = formatStreamingAnswer(normalizedText)
    }
  } catch (e) {
    console.error('Markdown解析失败:', e)
    html = formatStreamingAnswer(normalizedText)
  }
  
  return applyDoiLinksToHtml(html)
}

export function formatStreamingAnswer(text) {
  if (!text) return ''

  const escaped = escapeHtml(String(text))
  const normalized = escaped
    .replace(/\r\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')

  const html = normalized
    .replace(/^###\s+(.+)$/gm, '<h3>$1</h3>')
    .replace(/^##\s+(.+)$/gm, '<h2>$1</h2>')
    .replace(/^[-*+]\s+(.+)$/gm, '<div class="stream-bullet">• $1</div>')
    .replace(/^\d+[.)]\s+(.+)$/gm, '<div class="stream-bullet">$&</div>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\n/g, '<br>')

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

// 清理 LaTeX 公式
function cleanLaTeX(text) {
  text = text.replace(/\\\[[\s\S]*?\\\]/g, m => cleanLaTeXCommands(m.replace(/\\\[|\]/g, '')))
  text = text.replace(/\$\$[\s\S]*?\$\$/g, m => cleanLaTeXCommands(m.replace(/\$\$/g, '')))
  text = text.replace(/\\\([\s\S]*?\\\)/g, m => cleanLaTeXCommands(m.replace(/\\\(|\\\)/g, '')))
  text = text.replace(/\$[^$]+\$/g, m => cleanLaTeXCommands(m.replace(/\$/g, '')))
  return text
}

// 清理 LaTeX 命令
function cleanLaTeXCommands(text) {
  const subs = {'0':'₀','1':'₁','2':'₂','3':'₃','4':'₄','5':'₅','6':'₆','7':'₇','8':'₈','9':'₉'}
  const sups = {'0':'⁰','1':'¹','2':'²','3':'³','4':'⁴','5':'⁵','6':'⁶','7':'⁷','8':'⁸','9':'⁹'}
  
  text = text.replace(/_(\d+)/g, m => m.slice(1).split('').map(c => subs[c] || c).join(''))
  text = text.replace(/\^(\d+)/g, m => m.slice(1).split('').map(c => sups[c] || c).join(''))
  text = text.replace(/\\rightarrow/g, '→').replace(/\\leftarrow/g, '←')
  text = text.replace(/\\Rightarrow/g, '⇐').replace(/\\Leftarrow/g, '⇒')
  text = text.replace(/\\[a-zA-Z]+\{([^}]+)\}/g, '$1')
  text = text.replace(/\\[a-zA-Z]+/g, '')
  return text.trim()
}

// HTML 转义
export function escapeHtml(text) {
  const div = document.createElement('div')
  div.textContent = text
  return div.innerHTML
}
