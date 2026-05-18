import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const currentDir = dirname(fileURLToPath(import.meta.url))
const source = readFileSync(join(currentDir, 'main.css'), 'utf8')

function getCssRule(selector) {
  const escapedSelector = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const match = source.match(new RegExp(`${escapedSelector}\\s*\\{([\\s\\S]*?)\\}`))
  return match ? match[1] : ''
}

test('assistant message strong text keeps normal prose color instead of heading accent blue', () => {
  const strongRule = getCssRule('.message-content strong')

  assert.ok(strongRule, 'expected .message-content strong rule to exist')
  assert.doesNotMatch(strongRule, /color:\s*#667eea\s*;/i)
  assert.match(strongRule, /color:\s*inherit\s*;/i)
})
