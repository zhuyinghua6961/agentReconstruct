import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const currentDir = dirname(fileURLToPath(import.meta.url))
const source = readFileSync(join(currentDir, 'api.js'), 'utf8')

test('api maps graph_kb query mode to a human-readable knowledge-graph badge', () => {
  assert.match(source, /graph_kb:\s*'知识图谱'/)
})
