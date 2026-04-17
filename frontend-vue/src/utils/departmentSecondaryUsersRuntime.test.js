import test from 'node:test'
import assert from 'node:assert/strict'

import { createSecondaryUsersRuntime } from './departmentSecondaryUsersRuntime.js'

test('secondary users runtime expands once and reuses cached users on reopen', async () => {
  const calls = []
  const runtime = createSecondaryUsersRuntime({
    requestUsers: async (secondaryId) => {
      calls.push(secondaryId)
      return {
        success: true,
        data: {
          users: [
            { id: 1, username: 'alice', user_type: 3, user_type_label: '普通用户', status: 'active' },
          ],
        },
      }
    },
  })

  await runtime.toggle(11)
  assert.equal(runtime.isExpanded(11), true)
  assert.equal(calls.length, 1)
  assert.equal(runtime.loadingById.value[11], false)
  assert.equal(runtime.errorById.value[11], '')
  assert.equal(runtime.usersById.value[11][0].username, 'alice')

  await runtime.toggle(11)
  assert.equal(runtime.isExpanded(11), false)

  await runtime.toggle(11)
  assert.equal(runtime.isExpanded(11), true)
  assert.equal(calls.length, 1)
})

test('secondary users runtime turns transport failures into retryable error state', async () => {
  let shouldFail = true
  let calls = 0
  const runtime = createSecondaryUsersRuntime({
    requestUsers: async () => {
      calls += 1
      if (shouldFail) {
        throw new Error('offline')
      }
      return {
        success: true,
        data: {
          users: [
            { id: 2, username: 'bob', user_type: 2, user_type_label: '超级用户', status: 'disabled' },
          ],
        },
      }
    },
  })

  await runtime.toggle(11)
  assert.equal(runtime.isExpanded(11), true)
  assert.equal(runtime.loadingById.value[11], false)
  assert.match(runtime.errorById.value[11], /获取用户列表失败/)
  assert.equal(runtime.usersById.value[11], undefined)

  shouldFail = false
  await runtime.load(11, { force: true })
  assert.equal(calls, 2)
  assert.equal(runtime.errorById.value[11], '')
  assert.equal(runtime.loadingById.value[11], false)
  assert.equal(runtime.usersById.value[11][0].username, 'bob')
})
