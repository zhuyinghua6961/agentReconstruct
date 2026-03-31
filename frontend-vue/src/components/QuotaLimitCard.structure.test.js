import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const currentDir = dirname(fileURLToPath(import.meta.url))
const source = readFileSync(join(currentDir, 'QuotaLimitCard.vue'), 'utf8')

test('QuotaLimitCard exposes the expected prop and variant root class', () => {
  assert.match(source, /const props = defineProps\(\{/)
  assert.match(source, /card:\s*\{\s*type:\s*Object,\s*required:\s*true\s*\}/)
  assert.match(source, /class="quota-limit-card"/)
  assert.match(source, /:class="card\.variant"/)
})

test('QuotaLimitCard renders the expected content regions', () => {
  assert.match(source, /quota-headline/)
  assert.match(source, /quota-description/)
  assert.match(source, /quota-usage/)
  assert.match(source, /quota-reset/)
  assert.match(source, /v-if="card\.windows && card\.windows\.length > 0"/)
})

test('QuotaLimitCard renders the profile action link', () => {
  assert.match(source, /quota-action/)
  assert.match(source, /:href="card\.action\?\.to \|\| '\/profile'"/)
  assert.match(source, /{{ card\.action\?\.label \|\| '去个人中心查看配额' }}/)
})
