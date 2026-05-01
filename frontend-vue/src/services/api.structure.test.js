import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const currentDir = dirname(fileURLToPath(import.meta.url))
const source = readFileSync(join(currentDir, 'api.js'), 'utf8')
const chatStoreSource = readFileSync(join(currentDir, '..', 'stores', 'chatStore.js'), 'utf8')

test('api normalizeMessage keeps rich reference fields instead of shrinking to doi/title only', () => {
  assert.match(source, /return\s*\{\s*\.\.\.ref,/)
  assert.match(source, /metadata\.reference_objects/)
})

test('api reads refresh-survivable task rollout flag and keeps legacy ask_stream path available', () => {
  assert.match(source, /VITE_REFRESH_SURVIVABLE_QA_TASKS_ENABLED/)
  assert.match(source, /const askPath = \['fast', 'thinking', 'patent'\]\.includes\(normalizedMode\)\s*\? `\$\{V1\}\/\$\{normalizedMode\}\/ask_stream`/s)
})

test('api enables patent structured streaming capability for patent file and hybrid requests', async () => {
  global.localStorage = {
    getItem() {
      return null
    },
  }

  const originalFetch = global.fetch
  const captured = []
  global.fetch = async (url, options = {}) => {
    captured.push({ url: String(url || ''), options })
    if (String(url || '').includes('/ask_stream')) {
      return {
        ok: true,
        headers: new Headers({ 'content-type': 'text/event-stream' }),
        body: {
          getReader() {
            let done = false
            return {
              async read() {
                if (done) return { done: true, value: undefined }
                done = true
                return { done: true, value: undefined }
              },
            }
          },
        },
      }
    }
    return {
      ok: true,
      headers: new Headers({ 'content-type': 'application/json' }),
      async json() {
        return {
          task_id: 'task_preview_v1',
          status: 'queued',
          last_seq: 0,
          replay_available: true,
        }
      },
    }
  }

  try {
    const { api } = await import('./api.js')

    const stream = api.askStream(
      '总结文件',
      [],
      42,
      {
        all_available_ids: [11],
        selected_ids: [11],
        newly_uploaded_ids: [],
        last_focus_ids: [],
        last_turn_route: '',
      },
      undefined,
      'patent',
    )
    await stream.next()

    await api.createTask(
      '总结文件',
      [],
      42,
      {
        all_available_ids: [11],
        selected_ids: [11],
        newly_uploaded_ids: [],
        last_focus_ids: [],
        last_turn_route: '',
      },
      'patent',
      'client_preview_v1',
    )

    assert.equal(captured.length, 2)
    assert.equal(captured[0].options.headers['X-Patent-Stream-Capability'], 'preview_v1')
    assert.equal(
      JSON.parse(String(captured[1].options.body || '{}')).options.patent_stream_capability,
      'preview_v1',
    )
  } finally {
    global.fetch = originalFetch
  }
})

test('api exposes task endpoints behind the refresh-survivable QA task rollout surface', () => {
  assert.match(source, /refreshSurvivableQATasksEnabled:/)
  assert.match(source, /async createTask\(question, chatHistory = \[\], conversationId = null, pdfContext = null, mode = 'thinking', clientRequestId = ''\)/)
  assert.match(source, /requestJson\(`\$\{API_BASE\}\$\{V1\}\/v1\/tasks`/)
  assert.match(source, /async getTask\(taskId\)/)
  assert.match(source, /async streamTaskEvents\(taskId, afterSeq = 0, options = \{\}\)/)
  assert.match(source, /Accept:\s*'text\/event-stream'/)
  assert.match(source, /await streamSseJson\(\{ response, onEvent \}\)/)
  assert.match(source, /async getTaskEvents\(taskId, afterSeq = 0\)/)
  assert.match(source, /async cancelTask\(taskId\)/)
})

test('chatStore tracks the rollout flag for later task-path cutover work', () => {
  assert.match(chatStoreSource, /refreshSurvivableQATasksEnabled/)
})

test('api exposes document-level translation for the PdfReader full-document tab', () => {
  assert.match(source, /async translateDocument\(documentType, documentId\)/)
  assert.match(source, /requestJson\(`\$\{API_BASE\}\$\{V1\}\/translate_document`, \{/)
  assert.match(source, /body: JSON\.stringify\(\{\s*document_type: String\(documentType \|\| ''\),\s*document_id: String\(documentId \|\| ''\),\s*\}\)/s)
})

test('api exposes streaming document translation for incremental full-document rendering', () => {
  assert.match(source, /import \{ streamSseJson \} from '\.\.\/utils\/sse\.js'/)
  assert.match(source, /async translateDocumentStream\(documentType, documentId, options = \{\}\)/)
  assert.match(source, /const onEvent = typeof options\.onEvent === 'function' \? options\.onEvent : \(\) => \{\}/)
  assert.match(source, /const response = await fetch\(`\$\{API_BASE\}\$\{V1\}\/translate_document`, \{/)
  assert.match(source, /Accept:\s*'text\/event-stream'/)
  assert.match(source, /signal:\s*options\.signal/)
  assert.match(source, /await streamSseJson\(\{ response, onEvent \}\)/)
})

test('api normalizeMessage preserves terminal failure fields from conversation detail payloads', () => {
  assert.match(source, /const terminalStatus = String\(item\?\.terminalStatus \|\| item\?\.terminal_status \|\| item\?\.status \|\| metadata\?\.terminal_status \|\| metadata\?\.status \|\| ''\)\.trim\(\)/)
  assert.match(source, /const failureMessage = String\(item\?\.failureMessage \|\| item\?\.failure_message \|\| metadata\?\.failure_message \|\| ''\)\.trim\(\)/)
  assert.match(source, /const failureCode = String\(item\?\.failureCode \|\| item\?\.failure_code \|\| metadata\?\.failure_code \|\| ''\)\.trim\(\)/)
  assert.match(source, /const doneSeen = item\?\.doneSeen \?\? item\?\.done_seen \?\? metadata\?\.done_seen/)
  assert.match(source, /const timings = item\?\.timings \?\? metadata\?\.timings \?\? metadata\?\.stage_timings_ms/)
  assert.match(source, /metadata\.terminal_status = terminalStatus/)
  assert.match(source, /metadata\.failure_message = failureMessage/)
  assert.match(source, /metadata\.failure_code = failureCode/)
  assert.match(source, /metadata\.done_seen = Boolean\(doneSeen\)/)
  assert.match(source, /metadata\.timings = \{ \.\.\.timings \}/)
  assert.match(source, /\.\.\.\(metadata\.timings \? \{ timings: metadata\.timings \} : \{\}\)/)
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
