import katex from 'katex'
import { marked } from 'marked'
import { normalizeAnswerMarkdown } from '../../utils/index.js'
import { createMarkedOptions } from '../../utils/markdownMarkedOptions.js'

const KATEX_OPTIONS = {
  throwOnError: false,
  trust: false,
  strict: 'warn',
  output: 'htmlAndMathml',
}

export function parseMarkdownContent(content, options = {}) {
  const normalized = normalizeAnswerMarkdown(String(content || ''), { renderMath: false })
  const diagnostics = {
    rawHtmlTokenCount: 0,
    mathTokenCount: 0,
    doiLinkCount: 0,
    patentLinkCount: 0,
  }
  const tokens = decorateBlockTokens(marked.lexer(normalized, createMarkedOptions()), diagnostics, options)
  return { normalized, tokens, diagnostics }
}

export function renderMarkdownContentToHtml(content, options = {}) {
  return renderMarkdownTokensToHtml(parseMarkdownContent(content, options).tokens)
}

export function renderMarkdownTokensToHtml(tokens) {
  return (Array.isArray(tokens) ? tokens : [])
    .map((token) => renderBlockTokenToHtml(token))
    .join('')
}

export function renderKatexToHtml(text, displayMode = false) {
  const source = String(text || '')
  try {
    return katex.renderToString(source, {
      ...KATEX_OPTIONS,
      displayMode,
    })
  } catch (_error) {
    return `<code class="math-error">${escapeHtml(source)}</code>`
  }
}

function decorateBlockTokens(tokens, diagnostics, options = {}) {
  return (Array.isArray(tokens) ? tokens : []).map((token) => decorateBlockToken(token, diagnostics, options))
}

function decorateBlockToken(token, diagnostics, options = {}) {
  if (!token || typeof token !== 'object') return token

  if (token.type === 'html') {
    diagnostics.rawHtmlTokenCount += 1
    return { ...token, text: token.raw || token.text || '' }
  }

  if (token.type === 'paragraph') {
    const displayMath = readDisplayMathBlock(token.text)
    if (displayMath) {
      diagnostics.mathTokenCount += 1
      return {
        type: 'math',
        raw: token.raw,
        text: displayMath.text,
        display: true,
      }
    }
  }

  if (Array.isArray(token.tokens)) {
    return {
      ...token,
      tokens: token.type === 'code'
        ? token.tokens
        : decorateInlineOrBlockTokens(token.tokens, diagnostics, options),
    }
  }

  if (token.type === 'list' && Array.isArray(token.items)) {
    return {
      ...token,
      items: token.items.map((item) => decorateBlockToken(item, diagnostics, options)),
    }
  }

  if (token.type === 'table') {
    return decorateTableToken(token, diagnostics, options)
  }

  return token
}

function decorateInlineOrBlockTokens(tokens, diagnostics, options = {}) {
  return (Array.isArray(tokens) ? tokens : []).flatMap((token) => {
    if (!token || typeof token !== 'object') return []
    if (isInlineToken(token)) return decorateInlineToken(token, diagnostics, options)
    return [decorateBlockToken(token, diagnostics, options)]
  })
}

function decorateInlineToken(token, diagnostics, options = {}) {
  if (!token || typeof token !== 'object') return []
  if (token.type === 'text' && !Array.isArray(token.tokens)) {
    return splitTextToInlineTokens(token.text ?? token.raw ?? '', diagnostics)
  }
  if (token.type === 'html') {
    diagnostics.rawHtmlTokenCount += 1
    return [{ type: 'text', raw: token.raw || token.text || '', text: token.raw || token.text || '' }]
  }
  if (token.type === 'codespan') {
    return [token]
  }
  if (token.type === 'link') {
    return [token]
  }
  if (Array.isArray(token.tokens)) {
    return [{
      ...token,
      tokens: decorateInlineOrBlockTokens(token.tokens, diagnostics, options),
    }]
  }
  return [token]
}

function decorateTableToken(token, diagnostics, options = {}) {
  const decorateCell = (cell) => ({
    ...cell,
    tokens: decorateInlineOrBlockTokens(cell?.tokens || [], diagnostics, options),
  })
  return {
    ...token,
    header: (token.header || []).map(decorateCell),
    rows: (token.rows || []).map((row) => row.map(decorateCell)),
  }
}

function isInlineToken(token) {
  return [
    'text',
    'escape',
    'link',
    'image',
    'strong',
    'em',
    'codespan',
    'br',
    'del',
    'html',
  ].includes(token?.type)
}

function splitTextToInlineTokens(text, diagnostics) {
  const source = repairMergedDoiIdentifiers(String(text || ''))
  const tokens = []
  let buffer = ''
  let i = 0

  const flush = () => {
    if (!buffer) return
    tokens.push({ type: 'text', raw: buffer, text: buffer })
    buffer = ''
  }

  while (i < source.length) {
    const math = readInlineMathSegment(source, i)
    if (math) {
      flush()
      diagnostics.mathTokenCount += 1
      tokens.push(math)
      i = math.end
      continue
    }

    const doi = readDoiSegment(source, i)
    if (doi) {
      flush()
      if (doi.prefix) tokens.push({ type: 'text', raw: doi.prefix, text: doi.prefix })
      tokens.push({ type: 'doiLink', raw: doi.raw, doi: doi.normalized, text: doi.normalized })
      if (doi.suffix) tokens.push({ type: 'text', raw: doi.suffix, text: doi.suffix })
      diagnostics.doiLinkCount += 1
      i = doi.end
      continue
    }

    const patent = readPatentSegment(source, i)
    if (patent) {
      flush()
      if (patent.prefix) tokens.push({ type: 'text', raw: patent.prefix, text: patent.prefix })
      tokens.push({ type: 'patentLink', raw: patent.raw, patentId: patent.normalized, text: patent.normalized })
      if (patent.suffix) tokens.push({ type: 'text', raw: patent.suffix, text: patent.suffix })
      diagnostics.patentLinkCount += 1
      i = patent.end
      continue
    }

    buffer += source[i]
    i += 1
  }

  flush()
  return tokens
}

function readDisplayMathBlock(text) {
  const source = String(text || '').trim()
  if (source.startsWith('$$') && source.endsWith('$$') && source.length > 4) {
    return { text: source.slice(2, -2).trim() }
  }
  if (source.startsWith('\\[') && source.endsWith('\\]') && source.length > 4) {
    return { text: source.slice(2, -2).trim() }
  }
  return null
}

function readInlineMathSegment(text, startIndex) {
  const source = String(text || '')
  if (source.startsWith('$$', startIndex)) {
    const end = source.indexOf('$$', startIndex + 2)
    if (end < 0) return null
    const inner = source.slice(startIndex + 2, end)
    if (!isProbablyInlineMathContent(inner)) return null
    return {
      type: 'inlineMath',
      raw: source.slice(startIndex, end + 2),
      text: inner.trim(),
      display: false,
      end: end + 2,
    }
  }
  if (source.startsWith('\\(', startIndex)) {
    const end = source.indexOf('\\)', startIndex + 2)
    if (end < 0) return null
    const inner = source.slice(startIndex + 2, end)
    if (!isProbablyInlineMathContent(inner)) return null
    return {
      type: 'inlineMath',
      raw: source.slice(startIndex, end + 2),
      text: inner.trim(),
      display: false,
      end: end + 2,
    }
  }
  if (source[startIndex] !== '$' || source[startIndex + 1] === '$') return null
  const end = findClosingDollar(source, startIndex + 1)
  if (end < 0) return null
  const inner = source.slice(startIndex + 1, end)
  if (!isProbablyInlineMathContent(inner)) return null
  return {
    type: 'inlineMath',
    raw: source.slice(startIndex, end + 1),
    text: inner.trim(),
    display: false,
    end: end + 1,
  }
}

function findClosingDollar(source, fromIndex) {
  for (let index = fromIndex; index < source.length; index += 1) {
    if (source[index] !== '$') continue
    if (source[index - 1] === '\\') continue
    if (source[index + 1] === '$') continue
    return index
  }
  return -1
}

function isProbablyInlineMathContent(inner) {
  const source = String(inner || '').trim()
  if (!source) return false
  if (/^[\d.,\s$€£¥]+$/.test(source)) return false
  if (/[_\\^={}]|\\[a-zA-Z]|[α-ωΑ-ΩΔ∑∫±≤≥×·∝]/.test(source)) return true
  return source.length > 80
}

function readDoiSegment(text, startIndex) {
  const source = String(text || '')
  if (source[startIndex] === '(' || source[startIndex] === '[') {
    const wrapped = readWrappedDoiSegment(source, startIndex)
    if (wrapped) return wrapped
  }
  if (isAsciiLetter(source[startIndex])) {
    const prefixed = readDoiPrefixedSpan(source, startIndex)
    if (prefixed) return prefixed
  }
  if (source[startIndex] === '1') {
    const plain = readPlainDoiSegment(source, startIndex)
    if (plain) return plain
  }
  return null
}

function readWrappedDoiSegment(text, startIndex) {
  const openChar = text[startIndex]
  const closeChar = openChar === '[' ? ']' : ')'
  const span = readEnclosedSpan(text, startIndex, openChar, closeChar)
  if (!span) return null
  const normalized = parseWrappedDoi(span.inner)
  if (!normalized) return null
  return {
    raw: span.raw,
    normalized,
    start: span.start,
    end: span.end,
    prefix: openChar,
    suffix: closeChar,
  }
}

function parseWrappedDoi(inner) {
  const trimmed = String(inner || '').trim()
  const prefixed = readDoiPrefixedSpan(trimmed, 0)
  if (prefixed && prefixed.start === 0 && prefixed.end === trimmed.length) return prefixed.normalized
  const plain = readPlainDoiSegment(trimmed, 0)
  if (plain && plain.start === 0 && plain.end === trimmed.length) return plain.normalized
  return null
}

function readDoiPrefixedSpan(text, startIndex) {
  const source = String(text || '')
  const lower = source.slice(startIndex).toLowerCase()
  if (!lower.startsWith('doi')) return null
  const before = startIndex > 0 ? source[startIndex - 1] : ''
  if (before && /[A-Za-z0-9]/.test(before)) return null

  let i = startIndex + 3
  while (i < source.length && /\s/.test(source[i])) i += 1
  if (![':', '=', '：'].includes(source[i])) return null
  i += 1
  while (i < source.length && /\s/.test(source[i])) i += 1

  const token = readDoiToken(source, i, { allowImplicitSeparator: true })
  if (!token) return null
  return {
    ...token,
    raw: source.slice(startIndex, token.end),
    prefix: source.slice(startIndex, i),
  }
}

function readPlainDoiSegment(text, startIndex) {
  const source = String(text || '')
  if (!source.startsWith('10.', startIndex)) return null
  const before = startIndex > 0 ? source[startIndex - 1] : ''
  if (before && !isDoiBoundary(before) && before !== '\n') return null
  if (['=', ':'].includes(before)) return null
  return readDoiToken(source, startIndex, { allowImplicitSeparator: true })
}

function readDoiToken(text, startIndex, options = {}) {
  const source = String(text || '')
  const allowImplicitSeparator = options.allowImplicitSeparator === true
  if (!source.startsWith('10.', startIndex)) return null

  let i = startIndex + 3
  while (i < source.length && isDigit(source[i])) i += 1
  if (i === startIndex + 3 || i >= source.length) return null

  let usedImplicitSeparator = false
  if (source[i] === '/' || source[i] === '_') {
    i += 1
  } else if (!allowImplicitSeparator || !/[A-Za-z0-9]/.test(source[i])) {
    return null
  } else {
    usedImplicitSeparator = true
  }

  const bodyStart = i
  let depth = 0
  while (i < source.length) {
    const char = source[i]
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
  while (end > startIndex && /[.,;:]+/.test(source[end - 1])) end -= 1
  if (end <= startIndex) return null

  if (usedImplicitSeparator) {
    const implicitBody = source.slice(bodyStart, end)
    if (implicitBody.length < 5 || !/^[A-Za-z]/.test(implicitBody)) return null
  }

  const normalized = normalizeDoiForLink(source.slice(startIndex, end))
  if (!normalized) return null
  return {
    raw: source.slice(startIndex, end),
    normalized,
    start: startIndex,
    end,
  }
}

function readPatentSegment(text, startIndex) {
  const source = String(text || '')
  const legacy = source.slice(startIndex).match(/^\(\s*patent_id\s*=\s*([A-Za-z0-9._/\-]+)\s*\)/i)
  if (legacy) {
    const normalized = normalizePatentIdForLink(legacy[1])
    if (isPatentPublicationNumber(normalized)) {
      return {
        raw: legacy[0],
        normalized,
        start: startIndex,
        end: startIndex + legacy[0].length,
        prefix: '(',
        suffix: ')',
      }
    }
  }

  if (!/[A-Za-z]/.test(source[startIndex])) return null
  const before = startIndex > 0 ? source[startIndex - 1] : ''
  if (before && !isPatentLeadingBoundary(before)) return null
  const token = readPatentToken(source, startIndex)
  if (!token) return null
  const after = source[token.end]
  if (after && /[A-Za-z0-9._/\-]/.test(after)) return null
  return token
}

function readPatentToken(text, startIndex) {
  const source = String(text || '')
  if (startIndex + 3 >= source.length) return null
  const prefix = source.slice(startIndex, startIndex + 2)
  if (!/^[A-Za-z]{2}$/.test(prefix)) return null

  let i = startIndex + 2
  while (i < source.length && isDigit(source[i])) i += 1
  if (i === startIndex + 2) return null
  if (i >= source.length || !/[A-Za-z]/.test(source[i])) return null
  i += 1
  if (i < source.length && isDigit(source[i])) i += 1

  const normalized = normalizePatentIdForLink(source.slice(startIndex, i))
  if (!isPatentPublicationNumber(normalized)) return null
  return {
    raw: source.slice(startIndex, i),
    normalized,
    start: startIndex,
    end: i,
  }
}

function normalizeDoiForLink(raw) {
  let value = String(raw || '').trim()
  if (!value) return ''
  value = value.replace(/[)\],;:]+$/g, '')
  if (value.includes('_') && !value.includes('/')) value = value.replace('_', '/')
  if (!value.includes('/') && /^10\.\d{1,9}[A-Za-z0-9]/i.test(value)) {
    value = value.replace(/^(10\.\d{1,9})(?=[A-Za-z0-9])/, '$1/')
  }
  return /^10\.\d{1,9}\//i.test(value) ? value : ''
}

function repairMergedDoiIdentifiers(text) {
  let repaired = String(text || '').replace(
    /(10\.\d{1,9}\/[-._;()/:A-Z0-9]+?)([)\]])(\d{4,9})\.([A-Za-z][-._;()/:A-Z0-9]*)/gi,
    (_match, first, separator, registrant, suffix) => `${first}${separator} 10.${registrant}/${suffix}`,
  )
  let previous = ''
  while (repaired !== previous) {
    previous = repaired
    repaired = repaired.replace(
      /(10\.\d{1,9}(?:\/|[A-Za-z])[A-Za-z0-9._;()/:+\-_()-]*?)(10\.\d{1,9}(?:\/|[A-Za-z]))/gi,
      '$1 $2',
    )
  }
  return repaired
}

function normalizePatentIdForLink(raw) {
  return String(raw || '').trim().replace(/[)\],;:]+$/g, '').toUpperCase()
}

function isPatentPublicationNumber(value) {
  return /^[A-Z]{2}\d{6,14}[A-Z]\d?$/i.test(String(value || '').trim())
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
          raw: text.slice(startIndex, i + 1),
          inner: text.slice(startIndex + 1, i),
          start: startIndex,
          end: i + 1,
        }
      }
    }
  }
  return null
}

function isDigit(char) {
  return char >= '0' && char <= '9'
}

function isAsciiLetter(char) {
  const code = String(char || '').charCodeAt(0)
  return (code >= 65 && code <= 90) || (code >= 97 && code <= 122)
}

function isDoiBoundary(char) {
  return !char || /\s|[>"'([{<]/.test(char)
}

function isDoiBodyChar(char) {
  return /[A-Za-z0-9._;/:+\-_()-]/.test(char)
}

function isPatentLeadingBoundary(char) {
  return !char || /[\s([{<"'“‘《「『（【，。！？；：,.;!?:=\\|】【」』》]/.test(char)
}

function renderBlockTokenToHtml(token) {
  if (!token || typeof token !== 'object') return ''
  if (token.type === 'space') return ''
  if (token.type === 'hr') return '<hr>'
  if (token.type === 'heading') {
    const depth = Math.min(6, Math.max(1, Number(token.depth || 1)))
    return `<h${depth}>${renderInlineTokensToHtml(token.tokens || [])}</h${depth}>`
  }
  if (token.type === 'paragraph') {
    return `<p>${renderInlineTokensToHtml(token.tokens || [])}</p>`
  }
  if (token.type === 'text') {
    return renderInlineTokensToHtml(token.tokens || [token])
  }
  if (token.type === 'list') {
    const tag = token.ordered ? 'ol' : 'ul'
    const start = token.ordered && Number(token.start) > 1 ? ` start="${Number(token.start)}"` : ''
    return `<${tag}${start}>${(token.items || []).map((item) => renderBlockTokenToHtml(item)).join('')}</${tag}>`
  }
  if (token.type === 'list_item') {
    const body = renderMarkdownTokensToHtml(token.tokens || [])
    return `<li>${body}</li>`
  }
  if (token.type === 'blockquote') {
    return `<blockquote>${renderMarkdownTokensToHtml(token.tokens || [])}</blockquote>`
  }
  if (token.type === 'code') {
    const language = String(token.lang || '').trim().split(/\s+/)[0]
    const className = language ? ` class="language-${escapeAttribute(language)}"` : ''
    return `<pre><code${className}>${escapeHtml(token.text || '')}</code></pre>`
  }
  if (token.type === 'table') {
    const header = `<thead><tr>${(token.header || []).map((cell) => renderTableCellToHtml(cell, 'th')).join('')}</tr></thead>`
    const body = `<tbody>${(token.rows || []).map((row) => `<tr>${row.map((cell) => renderTableCellToHtml(cell, 'td')).join('')}</tr>`).join('')}</tbody>`
    return `<div class="markdown-table-scroll"><table>${header}${body}</table></div>`
  }
  if (token.type === 'html') {
    return `<p>${escapeHtml(token.text || token.raw || '')}</p>`
  }
  if (token.type === 'math') {
    return `<div class="math-block">${renderKatexToHtml(token.text, true)}</div>`
  }
  return ''
}

function renderTableCellToHtml(cell, tag) {
  const align = cell?.align ? ` style="text-align:${escapeAttribute(cell.align)}"` : ''
  return `<${tag}${align}>${renderInlineTokensToHtml(cell?.tokens || [])}</${tag}>`
}

function renderInlineTokensToHtml(tokens) {
  return (Array.isArray(tokens) ? tokens : []).map((token) => renderInlineTokenToHtml(token)).join('')
}

function renderInlineTokenToHtml(token) {
  if (!token || typeof token !== 'object') return ''
  if (token.type === 'text' || token.type === 'escape') return escapeHtml(token.text || token.raw || '')
  if (token.type === 'br') return '<br>'
  if (token.type === 'strong') return `<strong>${renderInlineTokensToHtml(token.tokens || [])}</strong>`
  if (token.type === 'em') return `<em>${renderInlineTokensToHtml(token.tokens || [])}</em>`
  if (token.type === 'del') return `<del>${renderInlineTokensToHtml(token.tokens || [])}</del>`
  if (token.type === 'codespan') return `<code>${escapeHtml(token.text || '')}</code>`
  if (token.type === 'link') {
    const href = safeLinkHref(token.href)
    const title = token.title ? ` title="${escapeAttribute(token.title)}"` : ''
    if (!href) return `<span>${renderInlineTokensToHtml(token.tokens || [])}</span>`
    return `<a href="${escapeAttribute(href)}"${title} target="_blank" rel="noreferrer noopener">${renderInlineTokensToHtml(token.tokens || [])}</a>`
  }
  if (token.type === 'doiLink') {
    return `<a href="#" class="doi-link" data-doi="${escapeAttribute(token.doi)}">${escapeHtml(token.text)}</a>`
  }
  if (token.type === 'patentLink') {
    return `<a href="#" class="doi-link patent-link" data-patent-id="${escapeAttribute(token.patentId)}">${escapeHtml(token.text)}</a>`
  }
  if (token.type === 'inlineMath') {
    return `<span class="math-inline">${renderKatexToHtml(token.text, false)}</span>`
  }
  if (token.type === 'html') return escapeHtml(token.text || token.raw || '')
  return escapeHtml(token.text || token.raw || '')
}

function safeLinkHref(href) {
  const value = String(href || '').trim()
  if (!value) return ''
  if (/^(?:https?:|mailto:|\/|#)/i.test(value)) return value
  return ''
}

export function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function escapeAttribute(value) {
  return escapeHtml(value).replace(/`/g, '&#96;')
}
