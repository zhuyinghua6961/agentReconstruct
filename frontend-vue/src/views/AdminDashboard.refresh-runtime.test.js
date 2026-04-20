import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const currentDir = dirname(fileURLToPath(import.meta.url))
const sourcePath = join(currentDir, 'AdminDashboard.vue')
const adminSource = existsSync(sourcePath) ? readFileSync(sourcePath, 'utf8') : ''

function extractHandlePersonnelManagementUpdatedBody(source) {
  const match = source.match(/async function handlePersonnelManagementUpdated\(\) \{([\s\S]*?)\n\}/)
  return match ? match[1] : ''
}

test('AdminDashboard handlePersonnelManagementUpdated eventually refreshes users via fetchUsers', async () => {
  const functionBody = extractHandlePersonnelManagementUpdatedBody(adminSource)

  assert.notEqual(functionBody, '')

  const factory = new Function(
    'runPersonnelManagementRefresh',
    'fetchUsers',
    `return async function handlePersonnelManagementUpdated() {${functionBody}\n}`,
  )

  const calls = []
  const handlePersonnelManagementUpdated = factory(
    async (callback) => {
      calls.push('runPersonnelManagementRefresh')
      await callback()
    },
    async () => {
      calls.push('fetchUsers')
    },
  )

  await handlePersonnelManagementUpdated()

  assert.deepEqual(calls, ['runPersonnelManagementRefresh', 'fetchUsers'])
})
