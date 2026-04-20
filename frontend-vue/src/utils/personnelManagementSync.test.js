import test from 'node:test'
import assert from 'node:assert/strict'

async function loadPersonnelManagementSyncUtils() {
  try {
    return await import('./personnelManagementSync.js')
  } catch {
    return {}
  }
}

test('runPersonnelManagementRefresh delegates to fetchUsers', async () => {
  const { runPersonnelManagementRefresh } = await loadPersonnelManagementSyncUtils()

  assert.equal(typeof runPersonnelManagementRefresh, 'function')

  const calls = []
  await runPersonnelManagementRefresh(async () => {
    calls.push('fetchUsers')
  })

  assert.deepEqual(calls, ['fetchUsers'])
})
