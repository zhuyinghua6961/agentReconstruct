import { Tokenizer } from 'marked'

class MarkdownTokenizer extends Tokenizer {
  del(src) {
    const source = String(src || '')
    if (!source.startsWith('~~')) return undefined
    const token = super.del(src)
    if (!token) return token
    const raw = String(token.raw || '')
    return raw.startsWith('~~') && raw.endsWith('~~') ? token : undefined
  }
}

export function createMarkedOptions(overrides = {}) {
  const tokenizer = overrides.tokenizer || new MarkdownTokenizer()
  return {
    breaks: false,
    gfm: true,
    tables: true,
    mangle: false,
    headerIds: false,
    ...overrides,
    tokenizer,
  }
}
