import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const currentDir = dirname(fileURLToPath(import.meta.url))
const source = readFileSync(join(currentDir, 'LiteratureSearch.vue'), 'utf8')

test('LiteratureSearch page wires search API and result selection', () => {
  assert.match(source, /import \{ buildPdfViewUrl, getLiteratureContent, searchLiterature \} from '\.\.\/api\/literature'/)
  assert.match(source, /searchLiterature\(/)
  assert.match(source, /getLiteratureContent\(/)
  assert.match(source, /queryType/)
  assert.match(source, /matchMode/)
  assert.doesNotMatch(source, /数据源/)
})

test('LiteratureSearch page includes navigation back to chat', () => {
  assert.match(source, /router\.push\('\/'\)/)
  assert.match(source, /返回问答/)
})

test('LiteratureSearch page shows relevance for semantic or reranked title results', () => {
  assert.match(source, /shouldShowRelevance/)
  assert.match(source, /relevanceLabel/)
  assert.match(source, /相关度/)
  assert.match(source, /rerank_applied/)
  assert.match(source, /主题全文/)
  assert.match(source, /全文语义/)
})
