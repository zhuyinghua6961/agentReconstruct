import test from 'node:test'
import assert from 'node:assert/strict'

async function loadPersonnelImportResultUtils() {
  try {
    return await import('./personnelImportResult.js')
  } catch {
    return {}
  }
}

test('personnel import result helpers count and filter created and updated rows separately', async () => {
  const {
    getPersonnelImportCreatedCount,
    filterPersonnelImportDetails,
    getPersonnelImportResultText,
    getPersonnelImportSuccessCount,
    getPersonnelImportUpdatedCount,
  } = await loadPersonnelImportResultUtils()

  assert.equal(typeof getPersonnelImportCreatedCount, 'function')
  assert.equal(typeof getPersonnelImportSuccessCount, 'function')
  assert.equal(typeof getPersonnelImportUpdatedCount, 'function')
  assert.equal(typeof filterPersonnelImportDetails, 'function')
  assert.equal(typeof getPersonnelImportResultText, 'function')

  assert.equal(getPersonnelImportCreatedCount({ created: 2, updated: 3, failed: 0 }), 2)
  assert.equal(getPersonnelImportSuccessCount({ created: 2, updated: 3, failed: 0 }), 2)
  assert.equal(getPersonnelImportUpdatedCount({ created: 2, updated: 3, failed: 0 }), 3)
  assert.deepEqual(
    filterPersonnelImportDetails(
      [
        { status: 'created', employee_no: 'T1' },
        { status: 'updated', employee_no: 'T2' },
        { status: 'failed', employee_no: 'T3' },
      ],
      'created',
    ).map(item => item.employee_no),
    ['T1'],
  )
  assert.deepEqual(
    filterPersonnelImportDetails(
      [
        { status: 'created', employee_no: 'T1' },
        { status: 'updated', employee_no: 'T2' },
        { status: 'failed', employee_no: 'T3' },
      ],
      'updated',
    ).map(item => item.employee_no),
    ['T2'],
  )
  assert.equal(getPersonnelImportResultText('created'), '新增')
  assert.equal(getPersonnelImportResultText('updated'), '更新')
})
