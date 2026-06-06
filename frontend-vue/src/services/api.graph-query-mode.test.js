import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const currentDir = dirname(fileURLToPath(import.meta.url))
const source = readFileSync(join(currentDir, 'api.js'), 'utf8')

test('api normalizes message query modes through the shared query mode helper', () => {
  assert.match(source, /import \{ resolveActualQueryModeLabel, resolveActualQueryModeRaw \} from '\.\.\/utils\/queryMode\.js'/)
  assert.match(source, /const rawMode = resolveActualQueryModeRaw\(item, metadata\)/)
  assert.match(source, /const queryMode = resolveActualQueryModeLabel\(item, metadata, \{ allowRouteFallback: false \}\)/)
})
