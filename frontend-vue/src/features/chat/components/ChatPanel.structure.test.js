import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const currentDir = dirname(fileURLToPath(import.meta.url))
const source = readFileSync(join(currentDir, 'ChatPanel.vue'), 'utf8')

test('ChatPanel uses the shared token markdown renderer instead of local html parsing', () => {
  assert.match(source, /import MarkdownRenderer from ['"]\.\.\/\.\.\/markdown\/MarkdownRenderer\.vue['"]/)
  assert.doesNotMatch(source, /formatAnswer/)
  assert.doesNotMatch(source, /marked\.setOptions\(\{\s*breaks:\s*true/)
  assert.doesNotMatch(source, /marked\.parse\(/)
  assert.doesNotMatch(source, /v-html=/)
  assert.match(source, /<MarkdownRenderer[\s\S]*:content="msg\.content \|\| ''"[\s\S]*variant="compact"/)
})
