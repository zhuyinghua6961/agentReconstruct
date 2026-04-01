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

test('api normalizeMessage preserves terminal failure fields from conversation detail payloads', () => {
  assert.match(source, /const terminalStatus = String\(item\?\.terminalStatus \|\| item\?\.terminal_status \|\| item\?\.status \|\| metadata\?\.terminal_status \|\| metadata\?\.status \|\| ''\)\.trim\(\)/)
  assert.match(source, /const failureMessage = String\(item\?\.failureMessage \|\| item\?\.failure_message \|\| metadata\?\.failure_message \|\| ''\)\.trim\(\)/)
  assert.match(source, /const failureCode = String\(item\?\.failureCode \|\| item\?\.failure_code \|\| metadata\?\.failure_code \|\| ''\)\.trim\(\)/)
  assert.match(source, /const doneSeen = item\?\.doneSeen \?\? item\?\.done_seen \?\? metadata\?\.done_seen/)
  assert.match(source, /metadata\.terminal_status = terminalStatus/)
  assert.match(source, /metadata\.failure_message = failureMessage/)
  assert.match(source, /metadata\.failure_code = failureCode/)
  assert.match(source, /metadata\.done_seen = Boolean\(doneSeen\)/)
})

test('api getConversationDetail also preserves frontend-shaped terminal fields when they appear in payloads', async () => {
  global.localStorage = {
    getItem() {
      return null
    },
  }

  const originalFetch = global.fetch
  global.fetch = async () => ({
    ok: true,
    async json() {
      return {
        success: true,
        data: {
          conversation_id: 42,
          title: '终态会话',
          messages: [
            {
              role: 'assistant',
              content: '',
              terminalStatus: 'canceled',
              failureMessage: '用户取消',
              failureCode: 'ASK_CANCELLED',
              doneSeen: false,
              retriable: false,
            },
          ],
          uploaded_files: [],
        },
      }
    },
  })

  try {
    const { api } = await import('./api.js')
    const detail = await api.getConversationDetail(42, 1)
    assert.equal(detail.messages[0].terminalStatus, 'canceled')
    assert.equal(detail.messages[0].failureMessage, '用户取消')
    assert.equal(detail.messages[0].failureCode, 'ASK_CANCELLED')
    assert.equal(detail.messages[0].doneSeen, false)
    assert.equal(detail.messages[0].retriable, false)
    assert.equal(detail.messages[0].metadata.terminal_status, 'canceled')
  } finally {
    global.fetch = originalFetch
  }
})
