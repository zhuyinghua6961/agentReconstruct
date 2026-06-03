import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const currentDir = dirname(fileURLToPath(import.meta.url))
const source = readFileSync(join(currentDir, 'ChatPanel.vue'), 'utf8')

test('ChatPanel uses the shared answer renderer instead of local marked line-break options', () => {
  assert.match(source, /import\s+\{\s*formatAnswer\s*\}\s+from ['"]\.\.\/\.\.\/\.\.\/utils['"]/)
  assert.doesNotMatch(source, /marked\.setOptions\(\{\s*breaks:\s*true/)
  assert.doesNotMatch(source, /marked\.parse\(/)
  assert.match(source, /html:\s*formatAnswer\(msg\.content \|\| ''\)/)
})
