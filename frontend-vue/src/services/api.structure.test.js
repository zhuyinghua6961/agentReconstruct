import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const currentDir = dirname(fileURLToPath(import.meta.url))
const source = readFileSync(join(currentDir, 'api.js'), 'utf8')

test('api normalizeMessage keeps rich reference fields instead of shrinking to doi/title only', () => {
  assert.match(source, /return\s*\{\s*\.\.\.ref,/)
  assert.match(source, /metadata\.reference_objects/)
})
