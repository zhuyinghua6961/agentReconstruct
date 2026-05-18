// 工具函数

import { marked } from 'marked'

marked.setOptions({
  breaks: true,
  gfm: true,
  tables: true,
  mangle: false,
  headerIds: false,
})

/** 避免将公式 / 方程中的 “-” 误判为 Markdown 列表切分 */
function looksInlineMathOrParamBlock(text) {
  const t = String(text || '')
  if (!t) return false
  if (/\\[a-zA-Z]+|\\frac|\\mathrm|\\times|\\cdot|\\sum|\\int|\\sqrt|\^\{|\^\d|_\{|_=|[α-ωΑ-Ω∑∫±≤≥×·Δ]/.test(t)) return true
  if (/[=≈∝]\s*[^\n，。]{0,120}/.test(t) && /[-−]\s*[^\n，。]{0,40}/.test(t) && /[α-ωσλεΔ]|[\\^_{}]/.test(t)) return true
  return false
}

function isProbablyInlineMathContent(inner) {
  const s = String(inner || '').trim()
  if (!s) return false
  if (/^[\d.,\s$€£¥]+$/.test(s)) return false
  if (s.length > 200) return true
  if (/[_\\^={}]|\\[a-zA-Z]|[α-ωΑ-ΩΔ∑∫±≤≥×·∝]/.test(s)) return true
  return false
}

function findClosingDollar(source, fromIndex) {
  const src = String(source || '')
  for (let j = fromIndex; j < src.length; j += 1) {
    if (src[j] !== '$') continue
    if (src[j - 1] === '\\') continue
    if (src[j + 1] === '$') continue
    return j
  }
  return -1
}

/**
 * 在 normalizeMarkdownForRender 之前遮蔽代码块与数学片段，
 * 防止行内 “：- a - b” 列表规范化破坏公式连续性。
 */
function maskMarkdownProtections(sourceText) {
  const src = String(sourceText || '')
  const segments = []
  let out = ''
  let i = 0
  let seq = 0
  const push = (start, end) => {
    const body = src.slice(start, end)
    const token = `\u27e6mdp${seq}\u27e7`
    seq += 1
    segments.push({ token, body })
    out += token
    i = end
  }

  while (i < src.length) {
    if (src.startsWith('```', i)) {
      const nl = src.indexOf('\n', i + 3)
      if (nl < 0) {
        out += src[i]
        i += 1
        continue
      }
      const close = src.indexOf('\n```', nl)
      const end = close >= 0 ? close + 4 : src.length
      push(i, end)
      continue
    }

    if (src.startsWith('$$', i)) {
      const end = src.indexOf('$$', i + 2)
      if (end < 0) {
        out += src[i]
        i += 1
        continue
      }
      push(i, end + 2)
      continue
    }

    if (src.startsWith('\\[', i)) {
      const end = src.indexOf('\\]', i + 2)
      if (end < 0) {
        out += src[i]
        i += 1
        continue
      }
      push(i, end + 2)
      continue
    }

    if (src.startsWith('\\(', i)) {
      const end = src.indexOf('\\)', i + 2)
      if (end < 0) {
        out += src[i]
        i += 1
        continue
      }
      push(i, end + 2)
      continue
    }

    if (src[i] === '$' && src[i + 1] !== '$') {
      const end = findClosingDollar(src, i + 1)
      if (end < 0 || !isProbablyInlineMathContent(src.slice(i + 1, end))) {
        out += src[i]
        i += 1
        continue
      }
      push(i, end + 1)
      continue
    }

    out += src[i]
    i += 1
  }

  return {
    text: out,
    restore(text) {
      let r = String(text || '')
      for (const { token, body } of segments) {
        r = r.split(token).join(body)
      }
      return r
    },
  }
}

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
  if (!value.includes('/') && /^10\.\d{1,9}[A-Za-z0-9]/i.test(value)) {
    value = value.replace(/^(10\.\d{1,9})(?=[A-Za-z0-9])/, '$1/')
  }
  return /^10\.\d{1,9}\//i.test(value) ? value : ''
}

function normalizePatentIdForLink(raw) {
  const value = String(raw || '').trim().replace(/[)\],;:]+$/g, '').toUpperCase()
  return /^[A-Z]{2}[A-Z0-9._/\-]+$/.test(value) ? value : ''
}

function isPatentPublicationNumber(value) {
  return /^[A-Z]{2}\d{6,14}[A-Z]\d?$/i.test(String(value || '').trim())
}

const PATENT_PUBLICATION_GLOBAL_RE = /\b[A-Za-z]{2}\d{6,14}[A-Za-z]\d?\b/g
const PATENT_ID_INLINE_CITATION_GLOBAL_RE = /\(\s*patent_id\s*=\s*([A-Za-z0-9._/\-]+)\s*\)/gi
const RENDERED_PATENT_CITATION_GLOBAL_RE = /[\(（]\s*([A-Za-z]{2}\d{6,14}[A-Za-z]\d?(?:\s*[,，;；、]\s*[A-Za-z]{2}\d{6,14}[A-Za-z]\d?)*)\s*[\)）]/gi
const BACKTICK_PATENT_SPAN_GLOBAL_RE = /`[^`\n]*[A-Za-z]{2}\d{6,14}[A-Za-z]\d?[^`\n]*`/gi
const BACKTICK_RENDERED_PATENT_CITATION_GLOBAL_RE = /`[\(（][^`\n]*[A-Za-z]{2}\d{6,14}[A-Za-z]\d?[^`\n]*[\)）]`/gi

function truncatePatentLogExcerpt(value, limit = 220) {
  const text = String(value || '').replace(/\s+/g, ' ').trim()
  if (text.length <= limit) return text
  return `${text.slice(0, Math.max(1, limit - 1)).trimEnd()}…`
}

function samplePatternMatches(pattern, text, limit = 5) {
  const source = String(text || '')
  const matcher = new RegExp(pattern.source, pattern.flags)
  const samples = []
  for (const match of source.matchAll(matcher)) {
    const sample = truncatePatentLogExcerpt(match[0], 120)
    if (sample && !samples.includes(sample)) {
      samples.push(sample)
    }
    if (samples.length >= limit) break
  }
  return samples
}

function collectPatentTextDiagnostics(text) {
  const source = String(text || '')
  const patentMatches = [...source.matchAll(new RegExp(PATENT_PUBLICATION_GLOBAL_RE.source, PATENT_PUBLICATION_GLOBAL_RE.flags))]
    .map((match) => String(match[0] || '').toUpperCase())
  const distinctPatentIds = []
  for (const patentId of patentMatches) {
    if (patentId && !distinctPatentIds.includes(patentId)) {
      distinctPatentIds.push(patentId)
    }
  }
  return {
    chars: source.length,
    patentPublicationCount: patentMatches.length,
    distinctPatentPublicationCount: distinctPatentIds.length,
    distinctPatentPublicationIdsSample: distinctPatentIds.slice(0, 8),
    patentIdInlineCitationCount: [...source.matchAll(new RegExp(PATENT_ID_INLINE_CITATION_GLOBAL_RE.source, PATENT_ID_INLINE_CITATION_GLOBAL_RE.flags))].length,
    renderedPatentCitationCount: [...source.matchAll(new RegExp(RENDERED_PATENT_CITATION_GLOBAL_RE.source, RENDERED_PATENT_CITATION_GLOBAL_RE.flags))].length,
    backtickPatentSpanCount: [...source.matchAll(new RegExp(BACKTICK_PATENT_SPAN_GLOBAL_RE.source, BACKTICK_PATENT_SPAN_GLOBAL_RE.flags))].length,
    backtickRenderedPatentCitationCount: [...source.matchAll(new RegExp(BACKTICK_RENDERED_PATENT_CITATION_GLOBAL_RE.source, BACKTICK_RENDERED_PATENT_CITATION_GLOBAL_RE.flags))].length,
    patentIdInlineCitationSamples: samplePatternMatches(PATENT_ID_INLINE_CITATION_GLOBAL_RE, source),
    renderedPatentCitationSamples: samplePatternMatches(RENDERED_PATENT_CITATION_GLOBAL_RE, source),
    backtickPatentSpanSamples: samplePatternMatches(BACKTICK_PATENT_SPAN_GLOBAL_RE, source),
    backtickRenderedPatentCitationSamples: samplePatternMatches(BACKTICK_RENDERED_PATENT_CITATION_GLOBAL_RE, source),
  }
}

function collectPatentHtmlDiagnostics(html) {
  const source = String(html || '')
  const anchorMatches = [...source.matchAll(/data-patent-id="([^"]+)"/g)].map((match) => String(match[1] || '').toUpperCase())
  const codePatentSpans = [...source.matchAll(/<code>([\s\S]*?)<\/code>/gi)]
    .map((match) => String(match[1] || ''))
    .filter((segment) => /[A-Za-z]{2}\d{6,14}[A-Za-z]\d?/i.test(segment))
  return {
    chars: source.length,
    patentAnchorCount: anchorMatches.length,
    patentAnchorSamples: anchorMatches.slice(0, 8),
    codePatentSpanCount: codePatentSpans.length,
    codePatentSpanSamples: codePatentSpans.slice(0, 8).map((segment) => truncatePatentLogExcerpt(segment, 120)),
  }
}

function logPatentRenderDiagnostics({ renderer, phase, originalText, normalizedText, html, fallbackUsed = false }) {
  const textDiagnostics = collectPatentTextDiagnostics(normalizedText)
  const htmlDiagnostics = collectPatentHtmlDiagnostics(html)
  const suspicious = (
    htmlDiagnostics.codePatentSpanCount > 0
    || (textDiagnostics.patentPublicationCount > 0 && htmlDiagnostics.patentAnchorCount === 0)
  )
  if (!suspicious) return
  console.warn('[patent-render-debug]', {
    renderer,
    phase,
    fallbackUsed,
    textDiagnostics,
    htmlDiagnostics,
    originalExcerpt: truncatePatentLogExcerpt(originalText, 320),
    normalizedExcerpt: truncatePatentLogExcerpt(normalizedText, 320),
  })
}

function isPatentLeadingBoundary(char) {
  return !char || /[\s([{<"'“‘《「『（【，。！？；：,.;!?:\\|、】【」』》]/.test(char)
}

function isPatentTrailingTokenChar(char) {
  return /[A-Za-z0-9._/\-]/.test(char)
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
    start: startIndex,
    end: i,
    normalized,
  }
}

function readWrappedPatentSegment(text, startIndex) {
  const openChar = text[startIndex]
  const closeChar = openChar === '[' ? ']' : (openChar === '（' ? '）' : ')')
  const span = readEnclosedSpan(text, startIndex, openChar, closeChar)
  if (!span) return null
  const normalized = normalizePatentIdForLink(span.inner)
  if (!isPatentPublicationNumber(normalized)) return null
  return {
    start: span.start,
    end: span.end,
    openChar,
    closeChar,
    normalized,
  }
}

function readPrefixedPatentSegment(text, startIndex) {
  const source = String(text || '')
  const prefix = source.startsWith('专利号', startIndex)
    ? '专利号'
    : (source.startsWith('公开号', startIndex) ? '公开号' : '')
  if (!prefix) return null
  const before = startIndex > 0 ? source[startIndex - 1] : ''
  if (before && /[A-Za-z0-9]/.test(before)) return null

  let i = startIndex + prefix.length
  while (i < source.length && /\s/.test(source[i])) i += 1
  const token = readPatentToken(source, i)
  if (!token) return null
  return {
    start: startIndex,
    end: token.end,
    prefix: source.slice(startIndex, i),
    normalized: token.normalized,
  }
}

function readPlainPatentSegment(text, startIndex) {
  const source = String(text || '')
  const before = startIndex > 0 ? source[startIndex - 1] : ''
  if (before && !isPatentLeadingBoundary(before)) return null
  const token = readPatentToken(source, startIndex)
  if (!token) return null
  const after = source[token.end]
  if (after && isPatentTrailingTokenChar(after)) return null
  return token
}

function readRawUrlSpan(text, startIndex) {
  const source = String(text || '')
  const matched = source.slice(startIndex).match(/^https?:\/\/[^\s<>"']+/i)
  if (!matched) return null
  return {
    start: startIndex,
    end: startIndex + matched[0].length,
    raw: matched[0],
  }
}

function repairMergedDoiIdentifiers(text) {
  let repaired = String(text || '').replace(
    /(10\.\d{1,9}\/[-._;()/:A-Z0-9]+?)([)\]])(\d{4,9})\.([A-Za-z][-._;()/:A-Z0-9]*)/gi,
    (_match, first, separator, registrant, suffix) => `${first}${separator} 10.${registrant}/${suffix}`
  )

  let previous = ''
  while (repaired !== previous) {
    previous = repaired
    repaired = repaired.replace(
      /(10\.\d{1,9}(?:\/|[A-Za-z])[A-Za-z0-9._;()/:+\-_()-]*?)(10\.\d{1,9}(?:\/|[A-Za-z]))/gi,
      '$1 $2'
    )
  }

  return repaired
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

function readDoiToken(text, startIndex, options = {}) {
  const allowImplicitSeparator = options.allowImplicitSeparator === true
  if (!String(text || '').startsWith('10.', startIndex)) return null
  let i = startIndex + 3
  while (i < text.length && isDigit(text[i])) i += 1
  if (i === startIndex + 3) return null
  if (i >= text.length) return null
  let usedImplicitSeparator = false
  if (['/', '_'].includes(text[i])) {
    i += 1
  } else if (!allowImplicitSeparator || !/[A-Za-z0-9]/.test(text[i])) {
    return null
  } else {
    usedImplicitSeparator = true
  }

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

  if (usedImplicitSeparator) {
    const implicitBody = text.slice(bodyStart, end)
    if (implicitBody.length < 5 || !/^[A-Za-z]/.test(implicitBody)) {
      return null
    }
  }

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
  if (['(', '[', '（'].includes(before)) return null

  let i = startIndex + 3
  while (i < text.length && /\s/.test(text[i])) i += 1
  if (![':', '=', '：'].includes(text[i])) return null
  i += 1
  while (i < text.length && /\s/.test(text[i])) i += 1

  const doiToken = readDoiToken(text, i, { allowImplicitSeparator: true })
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
  return readDoiToken(text, startIndex, { allowImplicitSeparator: true })
}

function linkifyDoiTextSegment(text) {
  const source = repairMergedDoiIdentifiers(text)
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

function renderPatentAnchor(rawPatentId) {
  const patentId = normalizePatentIdForLink(rawPatentId)
  if (!patentId || !isPatentPublicationNumber(patentId)) return null
  return `<a href="#" class="doi-link patent-link" data-patent-id="${patentId}">${patentId}</a>`
}

function parsePatentCitationIds(value) {
  const ids = []
  for (const rawPart of String(value || '').split(/\s*[,，;；、]\s*/)) {
    const patentId = normalizePatentIdForLink(rawPart)
    if (patentId && isPatentPublicationNumber(patentId) && !ids.includes(patentId)) {
      ids.push(patentId)
    }
  }
  return ids
}

function collectPatentCitationRanges(text) {
  const source = String(text || '')
  const ranges = []

  for (const match of source.matchAll(new RegExp(PATENT_ID_INLINE_CITATION_GLOBAL_RE.source, PATENT_ID_INLINE_CITATION_GLOBAL_RE.flags))) {
    const patentId = normalizePatentIdForLink(match[1])
    const start = Number(match.index ?? -1)
    if (!patentId || !isPatentPublicationNumber(patentId) || start < 0) continue
    ranges.push({
      start,
      end: start + match[0].length,
      patentIds: [patentId],
    })
  }

  for (const match of source.matchAll(new RegExp(RENDERED_PATENT_CITATION_GLOBAL_RE.source, RENDERED_PATENT_CITATION_GLOBAL_RE.flags))) {
    const patentIds = parsePatentCitationIds(match[1])
    const start = Number(match.index ?? -1)
    if (patentIds.length === 0 || start < 0) continue
    ranges.push({
      start,
      end: start + match[0].length,
      patentIds,
    })
  }

  return ranges.sort((a, b) => a.start - b.start || a.end - b.end)
}

function isInsidePatentCitationRange(index, citationRanges) {
  return citationRanges.some((range) => index >= range.start && index < range.end)
}

function hasLaterPatentCitation(citationRanges, rawPatentId, fromIndex) {
  const patentId = normalizePatentIdForLink(rawPatentId)
  if (!patentId || !isPatentPublicationNumber(patentId)) return false
  return citationRanges.some((range) => range.start >= fromIndex && range.patentIds.includes(patentId))
}

function stripHtmlTagsForPatentCitationScan(html) {
  return String(html || '').replace(/<[^>]+>/g, '')
}

function linkifyPatentTextSegment(text) {
  const source = String(text || '')
  const citationRanges = collectPatentCitationRanges(source)
  let output = ''
  let i = 0

  while (i < source.length) {
    const rawUrl = /[Hh]/.test(source[i]) ? readRawUrlSpan(source, i) : null
    if (rawUrl) {
      output += rawUrl.raw
      i = rawUrl.end
      continue
    }

    const legacyCitation = source.slice(i).match(/^\(\s*patent_id\s*=\s*([A-Za-z0-9._/\-]+)\s*\)/i)
    if (legacyCitation) {
      const anchor = renderPatentAnchor(legacyCitation[1])
      if (anchor) {
        output += `(${anchor})`
        i += legacyCitation[0].length
        continue
      }
    }

    const prefixed = readPrefixedPatentSegment(source, i)
    if (prefixed) {
      if (hasLaterPatentCitation(citationRanges, prefixed.normalized, prefixed.end)) {
        output += source.slice(prefixed.start, prefixed.end)
      } else {
        output += `${prefixed.prefix}${renderPatentAnchor(prefixed.normalized)}`
      }
      i = prefixed.end
      continue
    }

    const wrapped = ['(', '[', '（'].includes(source[i]) ? readWrappedPatentSegment(source, i) : null
    if (wrapped) {
      output += `${wrapped.openChar}${renderPatentAnchor(wrapped.normalized)}${wrapped.closeChar}`
      i = wrapped.end
      continue
    }

    const plain = /[A-Za-z]/.test(source[i]) ? readPlainPatentSegment(source, i) : null
    if (plain) {
      if (
        !isInsidePatentCitationRange(plain.start, citationRanges)
        && hasLaterPatentCitation(citationRanges, plain.normalized, plain.end)
      ) {
        output += source.slice(plain.start, plain.end)
      } else {
        output += renderPatentAnchor(plain.normalized)
      }
      i = plain.end
      continue
    }

    output += source[i]
    i += 1
  }

  return output
}

function linkifyInlinePatentCodeSpans(html) {
  return String(html || '')
    .split(/(<pre\b[\s\S]*?<\/pre>)/gi)
    .map((segment) => {
      if (/^<pre\b/i.test(segment)) return segment
      return segment.replace(
        /<code\b[^>]*>\s*([A-Za-z]{2}\d{6,14}[A-Za-z]\d?)\s*<\/code>/g,
        (raw, patentId, offset, fullSegment) => {
          const normalized = normalizePatentIdForLink(patentId)
          if (
            normalized
            && hasLaterPatentCitation(
              collectPatentCitationRanges(stripHtmlTagsForPatentCitationScan(fullSegment.slice(offset + raw.length))),
              normalized,
              0,
            )
          ) {
            return normalized
          }
          return renderPatentAnchor(patentId) || raw
        }
      )
    })
    .join('')
}

function applyPatentLinksToHtml(html) {
  const segments = linkifyInlinePatentCodeSpans(html).split(/(<[^>]+>)/g)
  let inAnchor = false
  let codeDepth = 0
  let preDepth = 0

  return segments
    .map((segment) => {
      if (!segment) return segment
      if (segment.startsWith('<')) {
        if (/^<a\b/i.test(segment)) {
          inAnchor = true
        } else if (/^<\/a\b/i.test(segment)) {
          inAnchor = false
        } else if (/^<code\b/i.test(segment)) {
          codeDepth += 1
        } else if (/^<\/code\b/i.test(segment)) {
          codeDepth = Math.max(0, codeDepth - 1)
        } else if (/^<pre\b/i.test(segment)) {
          preDepth += 1
        } else if (/^<\/pre\b/i.test(segment)) {
          preDepth = Math.max(0, preDepth - 1)
        }
        return segment
      }
      if (inAnchor || codeDepth > 0 || preDepth > 0) return segment
      return linkifyPatentTextSegment(segment)
    })
    .join('')
}

function applyCitationLinksToHtml(html) {
  return applyPatentLinksToHtml(applyDoiLinksToHtml(html))
}

function annotateMessageNotes(html) {
  return String(html || '').replace(
    /<p>(\s*注\*：[\s\S]*?)<\/p>/g,
    (_match, content) => `<p class="message-note">${content}</p>`
  )
}

function decorateRenderedAnswerHtml(html) {
  return annotateMessageNotes(applyCitationLinksToHtml(html))
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
    const plain = (wrapped || prefixed) ? null : (source[i] === '1' ? readPlainDoiSegment(source, i) : null)
    const match = wrapped || prefixed || plain

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

function looksLikeStructuredSectionHeading(line) {
  return /^(?:#{1,6}\s+)?[一二三四五六七八九十]+、.+$/.test(String(line || '').trim())
}

function splitStructuredSubheadingText(text) {
  const source = String(text || '').trim()
  const match = source.match(/^([^：:\n]{1,40}?[：:])(?:\s*(.*))?$/)
  if (!match) return null

  return {
    title: String(match[1] || '').trim(),
    body: String(match[2] || '').trim(),
  }
}

function normalizeStructuredSectionSubheadings(text) {
  const lines = String(text || '')
    .replace(/([。！？；;）)】\]])\s*(\d+[.)]\s+[^：:\n]{1,40}?[：:])/g, '$1\n$2')
    .split('\n')
  const normalized = []
  let inStructuredSection = false

  for (const rawLine of lines) {
    const line = String(rawLine || '')
    const trimmed = line.trim()

    if (!trimmed) {
      normalized.push('')
      continue
    }

    if (/^\s{0,3}#{1,6}\s+/.test(trimmed) && !looksLikeStructuredSectionHeading(trimmed)) {
      inStructuredSection = false
      normalized.push(line)
      continue
    }

    if (looksLikeStructuredSectionHeading(trimmed)) {
      inStructuredSection = true
      if (/^\s{0,3}#{1,6}\s+/.test(trimmed)) {
        normalized.push(trimmed)
      } else {
        normalized.push(`## ${trimmed}`)
      }
      continue
    }

    if (!inStructuredSection) {
      normalized.push(line)
      continue
    }

    const orderedItems = splitInlineOrderedItems(trimmed)
    const structuredItems = Array.isArray(orderedItems) && orderedItems.length > 0
      ? orderedItems.map((item) => ({
          marker: item.marker,
          parsed: splitStructuredSubheadingText(item.text),
        }))
      : null

    if (structuredItems && structuredItems.every((item) => item.parsed)) {
      for (const item of structuredItems) {
        normalized.push(`### ${item.marker} ${item.parsed.title}`)
        if (item.parsed.body) normalized.push(item.parsed.body)
      }
      continue
    }

    normalized.push(line)
  }

  return normalized.join('\n').replace(/\n{3,}/g, '\n\n')
}

function squareBracketDepth(text) {
  let depth = 0
  for (const ch of String(text || '')) {
    if (ch === '[') depth += 1
    else if (ch === ']') depth = Math.max(0, depth - 1)
  }
  return depth
}

function isMarkdownListItemLine(line) {
  return /^\s{0,3}(?:[-*+]|\d+[.)])\s+/.test(String(line || ''))
}

/**
 * 修复 LLM / 复制粘贴产生的「软换行」：GFM 会把 `4. 5-…` 当成新有序列表、
 * `10. 1007/…` 当成第 10 条列表、列表项后的 `+ …` 当成新无序列表，从而拆碎 DOI 与公式。
 * 仅在非 ``` 围栏段内做保守合并。
 */
function repairMarkdownSoftBreaksForRender(text) {
  let source = String(text || '').replace(/\r\n/g, '\n')
  source = source.replace(/\b10\.\s+(\d{4}\/)/g, '10.$1')

  const lines = source.split('\n')
  const out = []
  let inFence = false

  for (let i = 0; i < lines.length; i += 1) {
    const raw = lines[i]
    if (raw.trimStart().startsWith('```')) {
      inFence = !inFence
      out.push(raw)
      continue
    }
    if (inFence) {
      out.push(raw)
      continue
    }

    let acc = raw
    let j = i + 1
    while (j < lines.length) {
      const next = lines[j]
      if (next.trimStart().startsWith('```')) break

      const first = acc.split('\n')[0]
      const last = acc.split('\n').pop() || ''
      const lastTrim = last.trimEnd()
      const listHead = isMarkdownListItemLine(first)

      let merged = false
      if (squareBracketDepth(acc) > 0) {
        acc = `${acc} ${String(next).trimStart()}`
        merged = true
      } else if (
        listHead
        && /：\s*$/.test(lastTrim)
        && /^\s*\d{1,2}\.\s+\d/.test(next)
      ) {
        acc = `${acc} ${String(next).trimStart()}`
        merged = true
      } else if (
        listHead
        && lastTrim.includes('=')
        && lastTrim.endsWith(')')
        && /^\s*\+\s*\d/.test(next)
      ) {
        acc = `${acc} ${String(next).trimStart()}`
        merged = true
      } else if (listHead && /\+\s*$/.test(lastTrim) && /^\s*\+\s/.test(next)) {
        acc = `${acc} ${String(next).trimStart()}`
        merged = true
      }

      if (!merged) break
      j += 1
    }
    i = j - 1
    out.push(acc)
  }

  return out.join('\n')
}

function normalizeMarkdownForRender(text) {
  const crNorm = String(text || '')
    .replace(/\r\n/g, '\n')
    .replace(/\u00a0/g, ' ')
    .replace(/([。！？：:；;）)】\]])\s*(#{1,6}\s+)/g, '$1\n\n$2')

  const input = normalizeInlineMarkdownBoundaries(
    normalizeStructuredSectionSubheadings(repairMarkdownSoftBreaksForRender(crNorm))
  )

  const lines = input.split('\n')
  const normalized = []

  const isHeading = (line) => /^\s{0,3}#{1,6}\s+/.test(line)
  const isList = (line) => /^\s{0,3}(?:[-*+]\s+|\d+[.)]\s+)/.test(line)
  const isTable = (line) => line.includes('|') && !line.trim().startsWith('```')

  for (let index = 0; index < lines.length; index += 1) {
    const rawLine = lines[index]
    let line = String(rawLine || '').replace(/\t/g, '  ').replace(/[ \t]+$/g, '')
    const trimmed = line.trim()

    if (!trimmed) {
      if (normalized.length === 0 || normalized[normalized.length - 1] === '') continue
      normalized.push('')
      continue
    }

    line = normalizeInlineOrderedListLine(line)
    line = normalizeInlineBulletListLine(line)
    line = normalizeLeadingHeadingMarkers(line)
    line = normalizeMalformedHeadingLine(line)
    line = normalizeStandaloneOrderedSubheadingLine(line, findNextNonEmptyLine(lines, index + 1))
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

function normalizeInlineMarkdownBoundaries(text) {
  const lines = String(text || '').split('\n')
  const normalized = []

  for (let index = 0; index < lines.length; index += 1) {
    let line = String(lines[index] || '')

    line = line.replace(/([。！？：:；;）)】\]])\s+((?:[-*+]\s+.+))$/, (_match, prefix, inlineList) => {
      if (looksInlineMathOrParamBlock(inlineList)) return `${prefix} ${inlineList}`
      const items = splitInlineBulletItems(inlineList)
      if (!items || items.length < 2) return `${prefix} ${inlineList}`
      return `${prefix}\n\n${items.map(({ marker, text: itemText }) => `${marker} ${itemText}`).join('\n')}`
    })

    line = line.replace(/([。！？：:；;）)】\]])\s*((?:\d+[.)]\s+.+))$/, (_match, prefix, inlineList) => {
      if (looksInlineMathOrParamBlock(inlineList)) return `${prefix} ${inlineList}`
      const items = splitInlineOrderedItems(inlineList)
      if (!items) return `${prefix} ${inlineList}`
      return `${prefix}\n\n${items.map(({ marker, text: itemText }) => `${marker} ${itemText}`).join('\n')}`
    })

    line = line.replace(/^(.*\S)\s+(#{1,6}\s+.+)$/, (_match, prefix, headingBlock) => {
      const nextNonEmptyLine = findNextNonEmptyLine(lines, index + 1)
      if (!shouldSplitInlineHeadingBlock(prefix, headingBlock, nextNonEmptyLine)) return `${prefix} ${headingBlock}`
      return `${prefix}\n\n${headingBlock}`
    })

    line = line.replace(/^(.*\S)\s+(---\s+(?:(?:#{1,6})\s+)+.+)$/, (_match, prefix, separatorBlock) => {
      return `${prefix}\n\n${separatorBlock}`
    })

    line = line.replace(/(^|\s*---)\s+((?:(?:#{1,6})\s+)+.+)$/, (_match, separator, headingBlock) => {
      return `${separator}\n\n${headingBlock}`
    })

    normalized.push(line)
  }

  return normalized.join('\n')
}

function normalizeInlineBulletListLine(line) {
  const source = String(line || '')
  const triggerMatch = source.match(/^(.*?[：:；;])\s*([-*+])\s+(.+)$/)
  if (!triggerMatch) return source

  const inlineSource = `${triggerMatch[2]} ${triggerMatch[3].trim()}`
  if (looksInlineMathOrParamBlock(inlineSource)) return source

  const prefix = triggerMatch[1]
  const items = splitInlineBulletItems(inlineSource)
  if (!items) return source

  return [prefix, ...items.map(({ marker, text: itemText }) => `${marker} ${itemText}`)].join('\n')
}

function normalizeInlineOrderedListLine(line) {
  const source = String(line || '')
  if (looksInlineMathOrParamBlock(source)) return source

  const prefixedMatch = source.match(/^(.+?[：:；;])\s*(\d+[.)].+)$/)
  if (prefixedMatch) {
    const prefix = prefixedMatch[1]
    const items = splitInlineOrderedItems(prefixedMatch[2])
    if (items && items.length > 1) {
      return [prefix, ...items.map(({ marker, text: itemText }) => `${marker} ${itemText}`)].join('\n')
    }
  }

  const items = splitInlineOrderedItems(source)
  if (items && items.length > 1) {
    return items.map(({ marker, text: itemText }) => `${marker} ${itemText}`).join('\n')
  }

  return source
}

function normalizeLeadingHeadingMarkers(line) {
  return String(line || '').replace(
    /^(\s*)(?:(#{1,6})\s+)+(.*\S.*)$/,
    (_match, indent, hashes, text) => `${indent}${hashes} ${String(text || '').trim()}`
  )
}

function normalizeMalformedHeadingLine(line) {
  const source = String(line || '')
  if (!/^\s{0,3}#{1,6}\s+/.test(source)) return source

  return splitGluedHeadingMarkers(source)
    .flatMap((headingLine) => splitHeadingInlineBody(headingLine))
    .join('\n\n')
}

function splitGluedHeadingMarkers(line) {
  const parts = []
  let rest = String(line || '').trimEnd()

  while (rest) {
    const match = rest.match(/^(\s{0,3}#{1,6}\s+.+?)\s+(#{1,6}\s+\S[\s\S]*)$/)
    if (!match) {
      parts.push(rest)
      break
    }

    const firstTitle = stripHeadingMarker(match[1])
    const nextTitle = stripHeadingMarker(match[2])
    if (!isHeadingTitleCandidate(firstTitle) || !isHeadingTitleCandidate(nextTitle)) {
      parts.push(rest)
      break
    }

    parts.push(match[1].trimEnd())
    rest = match[2].trimStart()
  }

  return parts.length > 0 ? parts : [String(line || '')]
}

function splitHeadingInlineBody(line) {
  const source = String(line || '')
  const match = source.match(/^(\s{0,3}#{1,6}\s+)(.+)$/)
  if (!match) return [source]

  const content = String(match[2] || '').trim()
  for (const boundary of content.matchAll(/\s+/g)) {
    const title = content.slice(0, boundary.index).trim()
    const body = content.slice(Number(boundary.index) + boundary[0].length).trim()
    if (!isHeadingTitleCandidate(title) || !looksLikeInlineHeadingBody(body)) continue
    return [`${match[1]}${title}`, body]
  }

  return [source]
}

function stripHeadingMarker(line) {
  return String(line || '').replace(/^\s{0,3}#{1,6}\s+/, '').trim()
}

function isHeadingTitleCandidate(text) {
  const title = String(text || '').trim()
  if (title.length < 2 || title.length > 36) return false
  if (!/[\u4e00-\u9fff]/.test(title)) return false
  if (/[。！？!?；;，,]/.test(title)) return false
  return true
}

function looksLikeInlineHeadingBody(text) {
  const body = String(text || '').trim()
  if (body.length < 18) return false
  if (!/[\u4e00-\u9fff]/.test(body)) return false
  if (/^(?:#{1,6}|[-*+]|\d+[.)])\s+/.test(body)) return false
  if (/^(?:根据|例如|通过|追求|不同|常规|优化|元素|烧结|专利|该|这一|这些|这种|其|可|在|同时|此外|需要|通常|一般|主要|对于|实际应用|应用|材料|工艺|结构|性能|采用|利用|显示|表明|形成|包括|具有)/.test(body)) {
    return true
  }
  if (/^[\u4e00-\u9fffA-Za-z0-9/()（）+\-.]{2,18}(?:是|可|能|能够|有助于|会|通常|一般|主要|直接|间接|需要|影响|决定)/.test(body)) {
    return true
  }
  return /[，。；：]/.test(body.slice(0, 90))
    && /(?:专利|材料|密度|性能|工艺|电池|范围|策略|参数)/.test(body.slice(0, 90))
}

function normalizeStandaloneOrderedSubheadingLine(line, nextNonEmptyLine) {
  const source = String(line || '')
  const match = source.match(/^(\s{0,3})(\d+[.)]\s+.{1,80}[：:])\s*$/)
  if (!match) return source
  if (!String(nextNonEmptyLine || '').trim()) return source
  return `${match[1]}### ${match[2]}`
}

function findNextNonEmptyLine(lines, startIndex) {
  for (let index = startIndex; index < lines.length; index += 1) {
    const candidate = String(lines[index] || '').trim()
    if (candidate) return candidate
  }
  return ''
}

function looksLikeStructuredMarkdownLine(line) {
  return /^(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|\|.+\||---+\s*$)/.test(String(line || '').trim())
}

function shouldSplitInlineHeadingBlock(prefix, headingBlock, nextNonEmptyLine) {
  const prefixText = String(prefix || '').trim()
  const headingText = String(headingBlock || '').replace(/^#{1,6}\s+/, '').trim()
  if (!looksLikeStructuredMarkdownLine(nextNonEmptyLine)) return false
  if (!prefixText || !headingText) return false
  if (/[。！？!?；;：:.]$/.test(prefixText)) return false
  if (/[。！？!?；;：:]$/.test(headingText)) return false
  return true
}

function splitInlineBulletItems(text) {
  const source = String(text || '').trim()
  if (!source) return null

  const items = []
  let index = 0
  while (index < source.length) {
    while (index < source.length && /\s/.test(source[index])) index += 1
    if (index >= source.length) break

    const marker = source[index]
    if (!['-', '*', '+'].includes(marker)) return null
    index += 1

    while (index < source.length && /\s/.test(source[index])) index += 1
    if (index >= source.length) return null

    const itemStart = index
    let itemEnd = source.length

    for (let cursor = index; cursor < source.length; cursor += 1) {
      if (!/\s/.test(source[cursor])) continue
      let lookahead = cursor
      while (lookahead < source.length && /\s/.test(source[lookahead])) lookahead += 1
      if (
        lookahead < source.length
        && ['-', '*', '+'].includes(source[lookahead])
        && /\s/.test(source[lookahead + 1] || '')
        && shouldSplitInlineBulletBoundary(source, lookahead)
      ) {
        itemEnd = cursor
        index = lookahead
        break
      }
    }

    if (itemEnd === source.length) {
      index = source.length
    }

    const itemText = source.slice(itemStart, itemEnd).trim()
    if (!itemText) return null
    items.push({ marker, text: itemText })
  }

  return items.length >= 1 ? items : null
}

function splitInlineOrderedItems(text) {
  const source = String(text || '').trim()
  if (!source) return null
  if (!/^\d+[.)]/.test(source)) return null

  const items = []
  let index = 0

  while (index < source.length) {
    while (index < source.length && /\s/.test(source[index])) index += 1
    if (index >= source.length) break

    const markerMatch = source.slice(index).match(/^(\d+[.)])/)
    if (!markerMatch) return null
    const marker = markerMatch[1]
    index += marker.length

    while (index < source.length && /\s/.test(source[index])) index += 1
    if (index >= source.length) return null

    const itemStart = index
    let itemEnd = source.length

    for (let cursor = index; cursor < source.length; cursor += 1) {
      if (!/\d/.test(source[cursor])) continue
      const nextMarkerMatch = source.slice(cursor).match(/^(\d+[.)])/)
      if (!nextMarkerMatch) continue
      if (shouldSplitInlineOrderedBoundary(source, cursor)) {
        itemEnd = cursor
        index = cursor
        break
      }
    }

    if (itemEnd === source.length) {
      index = source.length
    }

    const itemText = source.slice(itemStart, itemEnd).trim()
    if (!itemText) return null
    items.push({ marker, text: itemText })
  }

  return items.length >= 1 ? items : null
}

function shouldSplitInlineBulletBoundary(source, markerIndex) {
  let contentStart = markerIndex + 1
  while (contentStart < source.length && /\s/.test(source[contentStart])) contentStart += 1
  if (contentStart >= source.length) return false

  return !/^[A-Za-z0-9](?:\s|$)/.test(source.slice(contentStart))
}

function shouldSplitInlineOrderedBoundary(source, markerIndex) {
  const before = String(source || '').slice(0, markerIndex).replace(/\s+$/g, '')
  const prevChar = before[before.length - 1] || ''
  if (/[。！？!?；;]/.test(prevChar)) return true
  return /(?:[\(（\[]\s*(?:(?:[A-Za-z]{2}\d{6,14}[A-Za-z]\d?|10\.\d{1,9}[-._;()/:A-Z0-9]+)\s*(?:[,，、;；]\s*(?:[A-Za-z]{2}\d{6,14}[A-Za-z]\d?|10\.\d{1,9}[-._;()/:A-Z0-9]+)\s*)*)[\)）\]])$/i.test(before)
}

function containsStructuredMarkdown(text) {
  return /(^|\n)\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|\|.+\||---+\s*$)/m.test(String(text || ''))
}

function containsInlineRenderMarkup(text) {
  return /<(?:sub|sup|span)\b/i.test(String(text || ''))
}

function looksLikeUnrenderedMarkdown(text, html) {
  if (!containsStructuredMarkdown(text)) return false
  if (/<(?:h[1-6]|ul|ol|li|table|blockquote|hr)\b/i.test(String(html || ''))) return false
  return /(?:^|\n)\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|---+\s*$)/m.test(String(text || ''))
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
  return marked.parse(text)
}

function formatStreamingFallback(text) {
  const escaped = escapeHtml(String(text))
  const normalized = escaped
    .replace(/\r\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')

  return normalized
    .replace(/^\s*---+\s*$/gm, '<hr>')
    .replace(/^(#{1,6})\s+(.+)$/gm, (_match, hashes, title) => `<h${hashes.length}>${title}</h${hashes.length}>`)
    .replace(/^[-*+]\s+(.+)$/gm, '<div class="stream-bullet">• $1</div>')
    .replace(/^\d+[.)]\s+(.+)$/gm, '<div class="stream-bullet">$&</div>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\n/g, '<br>')
}

function normalizeAnswerMarkdown(text, options = {}) {
  const { renderMath = true } = options
  const prot = maskMarkdownProtections(text)
  let normalizedText = normalizeMarkdownForRender(prot.text)
  normalizedText = prot.restore(normalizedText)
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
  let fallbackUsed = false
  try {
    html = renderMarkdownToHtml(normalizedText)
    if (looksLikeUnrenderedMarkdown(normalizedText, html)) {
      html = formatStreamingFallback(normalizedText)
      fallbackUsed = true
    }
  } catch (e) {
    console.error('Markdown解析失败:', e)
    html = formatStreamingFallback(normalizedText)
    fallbackUsed = true
  }

  const decoratedHtml = decorateRenderedAnswerHtml(html)
  logPatentRenderDiagnostics({
    renderer: 'formatAnswer',
    phase: 'final',
    originalText: text,
    normalizedText,
    html: decoratedHtml,
    fallbackUsed,
  })
  return decoratedHtml
}

export function formatStreamingAnswer(text) {
  if (!text) return ''

  const baseText = normalizeAnswerMarkdown(text, { renderMath: false })
  const shouldRenderMath = containsMathMarkup(baseText) || containsInlineRenderMarkup(baseText)

  if (!containsStructuredMarkdown(baseText) && !shouldRenderMath) {
    const fallbackHtml = decorateRenderedAnswerHtml(formatStreamingFallback(baseText))
    logPatentRenderDiagnostics({
      renderer: 'formatStreamingAnswer',
      phase: 'fallback',
      originalText: text,
      normalizedText: baseText,
      html: fallbackHtml,
      fallbackUsed: true,
    })
    return fallbackHtml
  }

  const normalizedText = shouldRenderMath ? renderMathMarkup(baseText) : baseText
  let html = ''
  let fallbackUsed = false

  try {
    html = renderMarkdownToHtml(normalizedText)
    if (looksLikeUnrenderedMarkdown(normalizedText, html)) {
      html = formatStreamingFallback(normalizedText)
      fallbackUsed = true
    }
  } catch (e) {
    console.error('流式Markdown解析失败:', e)
    html = formatStreamingFallback(normalizedText)
    fallbackUsed = true
  }

  const decoratedHtml = decorateRenderedAnswerHtml(html)
  logPatentRenderDiagnostics({
    renderer: 'formatStreamingAnswer',
    phase: 'streaming',
    originalText: text,
    normalizedText,
    html: decoratedHtml,
    fallbackUsed,
  })
  return decoratedHtml
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
