import test from 'node:test'
import assert from 'node:assert/strict'

import { createDepartmentUsersRuntime } from './departmentSecondaryUsersRuntime.js'

test('department users runtime expands once and reuses cached members on reopen', async () => {
  const calls = []
  const runtime = createDepartmentUsersRuntime({
    requestUsers: async (nodeKey) => {
      calls.push(nodeKey)
      return {
        success: true,
        data: {
          members: [
            { id: 1, employee_no: 'P001', full_name: '张三', status: 'active' },
          ],
        },
      }
    },
  })

  await runtime.toggle('111')
  assert.equal(runtime.isExpanded('111'), true)
  assert.equal(calls.length, 1)
  assert.equal(runtime.loadingById.value['111'], false)
  assert.equal(runtime.errorById.value['111'], '')
  assert.equal(runtime.usersById.value['111'][0].employee_no, 'P001')

  await runtime.toggle('111')
  assert.equal(runtime.isExpanded('111'), false)

  await runtime.toggle('111')
  assert.equal(runtime.isExpanded('111'), true)
  assert.equal(calls.length, 1)
})

test('department users runtime supports stable string keys for synthetic legacy nodes', async () => {
  const calls = []
  const runtime = createDepartmentUsersRuntime({
    requestUsers: async (nodeKey) => {
      calls.push(nodeKey)
      return { success: true, data: { users: [] } }
    },
  })

  await runtime.toggle('legacy-secondary-11')
  assert.deepEqual(calls, ['legacy-secondary-11'])
})

test('department users runtime turns transport failures into retryable error state', async () => {
  let shouldFail = true
  let calls = 0
  const runtime = createDepartmentUsersRuntime({
    requestUsers: async () => {
      calls += 1
      if (shouldFail) {
        throw new Error('offline')
      }
      return {
        success: true,
        data: {
          users: [
            { id: 2, employee_no: 'P002', full_name: '李四', status: 'disabled' },
          ],
        },
      }
    },
  })

  await runtime.toggle('111')
  assert.equal(runtime.isExpanded('111'), true)
  assert.equal(runtime.loadingById.value['111'], false)
  assert.match(runtime.errorById.value['111'], /获取成员列表失败/)
  assert.equal(runtime.usersById.value['111'], undefined)

  shouldFail = false
  await runtime.load('111', { force: true })
  assert.equal(calls, 2)
  assert.equal(runtime.errorById.value['111'], '')
  assert.equal(runtime.loadingById.value['111'], false)
  assert.equal(runtime.usersById.value['111'][0].full_name, '李四')
})
