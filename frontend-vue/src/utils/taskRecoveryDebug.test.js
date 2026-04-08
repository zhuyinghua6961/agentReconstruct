import test from 'node:test'
import assert from 'node:assert/strict'

test('task recovery debug logging stays disabled by default and enables from localStorage', async () => {
  delete global.localStorage
  const { createTaskRecoveryDebugLogger } = await import('./taskRecoveryDebug.js')

  const silentCalls = []
  const silentLogger = createTaskRecoveryDebugLogger({
    sink: (entry) => {
      silentCalls.push(entry)
    },
  })

  assert.equal(silentLogger.isEnabled(), false)
  silentLogger.log('attach', { taskId: 'task_default_off' })
  assert.deepEqual(silentCalls, [])

  global.localStorage = {
    getItem(key) {
      if (String(key) === 'agentcode.task-recovery-debug') return '1'
      return null
    },
  }

  const enabledCalls = []
  const enabledLogger = createTaskRecoveryDebugLogger({
    sink: (entry) => {
      enabledCalls.push(entry)
    },
  })

  assert.equal(enabledLogger.isEnabled(), true)
  enabledLogger.log('attach', { taskId: 'task_enabled' })
  assert.equal(enabledCalls.length, 1)
  assert.equal(enabledCalls[0].scope, 'attach')
  assert.equal(enabledCalls[0].payload.taskId, 'task_enabled')
})

test('task recovery debug logger backfills client_request_id to real attach:start payloads', async () => {
  global.localStorage = {
    getItem(key) {
      if (String(key) === 'agentcode.task-recovery-debug') return '1'
      return null
    },
  }
  const { createTaskRecoveryDebugLogger } = await import('./taskRecoveryDebug.js')

  const calls = []
  const logger = createTaskRecoveryDebugLogger({
    sink: (entry) => {
      calls.push(entry)
    },
  })

  logger.log('send:start', {
    chatId: 'chat_1',
    clientRequestId: 'client_req_001',
  })
  logger.log('send:task-created', {
    chatId: 'chat_1',
    clientRequestId: 'client_req_001',
    taskId: 'task_001',
  })
  logger.log('attach:start', {
    chatId: 'chat_1',
    requestedTask: {
      taskId: 'task_001',
      status: 'queued',
      lastSeq: 0,
    },
  })

  assert.equal(calls.length, 3)
  assert.equal(calls[1].payload.clientRequestId, 'client_req_001')
  assert.equal(calls[1].payload.taskId, 'task_001')
  assert.equal(calls[2].payload.clientRequestId, 'client_req_001')
  assert.equal(calls[2].payload.taskId, 'task_001')
})
