import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const currentDir = dirname(fileURLToPath(import.meta.url))
const patentPage = readFileSync(join(currentDir, 'PatentSearch.vue'), 'utf8')
const homePage = readFileSync(join(currentDir, 'Home.vue'), 'utf8')
const routerSource = readFileSync(join(currentDir, '..', 'router', 'index.js'), 'utf8')
const apiSource = readFileSync(join(currentDir, '..', 'api', 'patent.js'), 'utf8')

test('PatentSearch page wires search API and result selection', () => {
  assert.match(patentPage, /import \{ buildPatentPdfUrl, fetchPdfDocumentByUrl, getPatentAbstract, searchPatent \} from '\.\.\/api\/patent'/)
  assert.match(patentPage, /searchPatent\(/)
  assert.match(patentPage, /getPatentAbstract\(/)
  assert.match(patentPage, /queryType/)
  assert.doesNotMatch(patentPage, /数据源/)
})

test('PatentSearch page includes navigation back to chat', () => {
  assert.match(patentPage, /router\.push\('\/'\)/)
  assert.match(patentPage, /返回问答/)
})

test('router registers patent search page', () => {
  assert.match(routerSource, /\/patent-search/)
  assert.match(routerSource, /PatentSearch/)
})

test('Home page links to patent search', () => {
  assert.match(homePage, /router-link to="\/patent-search"/)
  assert.match(homePage, /专利检索/)
})

test('patent API module exposes search and original helpers', () => {
  assert.match(apiSource, /patent_search/)
  assert.match(apiSource, /patent\/original/)
})
