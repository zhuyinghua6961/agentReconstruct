import test from 'node:test'
import assert from 'node:assert/strict'

async function loadPersonnelImportResultUtils() {
  try {
    return await import('./personnelImportResult.js')
  } catch {
    return {}
  }
}

test('personnel import result helpers count created and updated rows as success', async () => {
  const {
    filterPersonnelImportDetails,
    getPersonnelImportResultText,
    getPersonnelImportSuccessCount,
  } = await loadPersonnelImportResultUtils()

  assert.equal(typeof getPersonnelImportSuccessCount, 'function')
  assert.equal(typeof filterPersonnelImportDetails, 'function')
  assert.equal(typeof getPersonnelImportResultText, 'function')

  assert.equal(getPersonnelImportSuccessCount({ created: 2, updated: 3, failed: 0 }), 5)
  assert.deepEqual(
    filterPersonnelImportDetails(
      [
        { status: 'created', employee_no: 'T1' },
        { status: 'updated', employee_no: 'T2' },
        { status: 'failed', employee_no: 'T3' },
      ],
      'success',
    ).map(item => item.employee_no),
    ['T1', 'T2'],
  )
  assert.equal(getPersonnelImportResultText('created'), '新增')
  assert.equal(getPersonnelImportResultText('updated'), '更新')
})
