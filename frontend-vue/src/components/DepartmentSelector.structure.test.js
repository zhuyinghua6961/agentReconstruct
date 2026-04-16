import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const currentDir = dirname(fileURLToPath(import.meta.url))
const selectorPath = join(currentDir, 'DepartmentSelector.vue')
const selectorSource = existsSync(selectorPath) ? readFileSync(selectorPath, 'utf8') : ''

test('DepartmentSelector provides searchable department selection UI', () => {
  assert.match(selectorSource, /搜索部门/)
  assert.match(selectorSource, /searchKeyword|searchTerm|searchQuery/)
  assert.match(selectorSource, /primary-id|primaryId/)
  assert.match(selectorSource, /secondary-id|secondaryId/)
})
