// 工具函数

import { marked } from 'marked'

marked.setOptions({
  breaks: true,
  gfm: true,
  tables: true,
  mangle: false,
  headerIds: false,
})

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

function escapeRegExp(value) {
  return String(value || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function isPatentPublicationNumber(value) {
  return /^[A-Z]{2}\d{6,14}[A-Z]\d?$/i.test(String(value || '').trim())
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
      /(10\.\d{1,9}(?:\/|[A-Za-z0-9])[A-Za-z0-9._;()/:+\-_()-]*?)(10\.\d{1,9}(?:\/|[A-Za-z0-9]))/gi,
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
    if (
      i > bodyStart
      && char === '1'
      && /^10\.\d{1,9}(?:\/|_|[A-Za-z0-9])/.test(text.slice(i))
    ) break
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

function linkifyPatentTextSegment(text) {
  const renderPatentAnchor = (rawPatentId) => {
    const patentId = normalizePatentIdForLink(rawPatentId)
    if (!patentId) return null
    return `<a href="#" class="doi-link patent-link" data-patent-id="${patentId}">${patentId}</a>`
  }

  const source = String(text || '')
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
      output += `${prefixed.prefix}${renderPatentAnchor(prefixed.normalized)}`
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
      output += renderPatentAnchor(plain.normalized)
      i = plain.end
      continue
    }

    output += source[i]
    i += 1
  }

  return output
}

function applyPatentLinksToHtml(html) {
  const segments = String(html || '').split(/(<[^>]+>)/g)
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
  const input = normalizeInlineMarkdownBoundaries(
    String(text || '')
      .replace(/\r\n/g, '\n')
      .replace(/\u00a0/g, ' ')
      .replace(/([。！？：:])\s*(#{1,6}\s+)/g, '$1\n\n$2')
  )

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

    line = normalizeInlineOrderedListLine(line)
    line = normalizeInlineBulletListLine(line)
    line = normalizeLeadingHeadingMarkers(line)
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
      const items = splitInlineBulletItems(inlineList)
      if (!items || items.length < 2) return `${prefix} ${inlineList}`
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

  const prefix = triggerMatch[1]
  const inlineSource = `${triggerMatch[2]} ${triggerMatch[3].trim()}`
  const items = splitInlineBulletItems(inlineSource)
  if (!items) return source

  return [prefix, ...items.map(({ marker, text: itemText }) => `${marker} ${itemText}`)].join('\n')
}

function normalizeInlineOrderedListLine(line) {
  const source = String(line || '')

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
  return /[。！？!?；;]/.test(prevChar)
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
  let normalizedText = normalizeMarkdownForRender(text)
  normalizedText = dedupeTrailingPatentCitations(normalizedText)
  normalizedText = fixTableFormat(normalizedText)
  if (renderMath) {
    normalizedText = renderMathMarkup(normalizedText)
  }
  return normalizedText
}

function dedupeTrailingPatentCitations(text) {
  return String(text || '')
    .split('\n')
    .map((line) => dedupeTrailingPatentCitationsInLine(line))
    .join('\n')
}

function dedupeTrailingPatentCitationsInLine(line) {
  const source = String(line || '')
  if (!source) return source

  const wrappedCitationRe = /[\(（]\s*([A-Za-z]{2}\d{6,14}[A-Za-z]\d?)\s*[\)）]/gi
  const matches = Array.from(source.matchAll(wrappedCitationRe))
  if (matches.length === 0) return source

  let updated = source
  for (let i = matches.length - 1; i >= 0; i -= 1) {
    const match = matches[i]
    const rawPatentId = match[1]
    const patentId = normalizePatentIdForLink(rawPatentId)
    const start = Number(match.index ?? -1)
    if (!patentId || !isPatentPublicationNumber(patentId) || start < 0) continue

    const end = start + match[0].length
    const prefix = updated.slice(0, start)
    const suffix = updated.slice(end)
    if (!/^\s*[。！？!?；;，,、.]?\s*$/.test(suffix)) continue

    const prefixWithoutUrls = stripRawUrls(prefix)
    const priorPatentRe = new RegExp(`(?:patent_id\\s*=\\s*)?${escapeRegExp(patentId)}\\b`, 'i')
    if (!priorPatentRe.test(prefixWithoutUrls)) continue

    updated = `${prefix}${suffix}`
  }

  return updated
    .replace(/[ \t]{2,}/g, ' ')
    .replace(/\s+([，。！？；：,.;!?])/g, '$1')
    .replace(/[，,；;]\s*([。！？!?])/g, '$1')
    .replace(/\(\s+\)/g, '')
}

function stripRawUrls(text) {
  const source = String(text || '')
  let output = ''
  let i = 0

  while (i < source.length) {
    const codeSpan = source[i] === '`' ? readInlineCodeSpan(source, i) : null
    if (codeSpan) {
      output += ' '.repeat(codeSpan.end - codeSpan.start)
      i = codeSpan.end
      continue
    }

    const rawUrl = /[Hh]/.test(source[i]) ? readRawUrlSpan(source, i) : null
    if (rawUrl) {
      output += ' '.repeat(rawUrl.raw.length)
      i = rawUrl.end
      continue
    }
    output += source[i]
    i += 1
  }

  return output
}

function readInlineCodeSpan(text, startIndex) {
  const source = String(text || '')
  if (source[startIndex] !== '`') return null

  let tickCount = 1
  while (source[startIndex + tickCount] === '`') tickCount += 1
  const fence = '`'.repeat(tickCount)
  const endIndex = source.indexOf(fence, startIndex + tickCount)
  if (endIndex < 0) return null

  return {
    start: startIndex,
    end: endIndex + tickCount,
  }
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

  return decorateRenderedAnswerHtml(html)
}

export function formatStreamingAnswer(text) {
  if (!text) return ''

  const baseText = normalizeAnswerMarkdown(text, { renderMath: false })
  const shouldRenderMath = containsMathMarkup(baseText) || containsInlineRenderMarkup(baseText)

  if (!containsStructuredMarkdown(baseText) && !shouldRenderMath) {
    return decorateRenderedAnswerHtml(formatStreamingFallback(baseText))
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

  return decorateRenderedAnswerHtml(html)
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
