import test from 'node:test'
import assert from 'node:assert/strict'

async function loadFileSelectionUtils() {
  try {
    return await import('./fileSelection.js')
  } catch {
    return {}
  }
}

test('mergeSelectedFileIdsAfterUpload replaces previous selections with the newly uploaded file', async () => {
  const { mergeSelectedFileIdsAfterUpload } = await loadFileSelectionUtils()

  assert.equal(typeof mergeSelectedFileIdsAfterUpload, 'function')
  assert.deepEqual(
    mergeSelectedFileIdsAfterUpload([9, 2], 15),
    [15]
  )
})

test('mergeSelectedFileIdsAfterUpload removes invalid ids and avoids duplicates', async () => {
  const { mergeSelectedFileIdsAfterUpload } = await loadFileSelectionUtils()

  assert.equal(typeof mergeSelectedFileIdsAfterUpload, 'function')
  assert.deepEqual(
    mergeSelectedFileIdsAfterUpload([0, '3', 3, null, -2], '3'),
    [3]
  )
})

test('resolveUploadedFileDisplayNumber prefers display_no for upload success messages', async () => {
  const { resolveUploadedFileDisplayNumber } = await loadFileSelectionUtils()

  assert.equal(typeof resolveUploadedFileDisplayNumber, 'function')
  assert.equal(
    resolveUploadedFileDisplayNumber({ display_no: 3, file_no: 7, file_id: 157 }),
    3
  )
})

test('resolveUploadedFileDisplayNumber falls back to file_no when display_no is unavailable', async () => {
  const { resolveUploadedFileDisplayNumber } = await loadFileSelectionUtils()

  assert.equal(typeof resolveUploadedFileDisplayNumber, 'function')
  assert.equal(
    resolveUploadedFileDisplayNumber({ display_no: 0, file_no: 7, file_id: 157 }),
    7
  )
})

test('resolveUploadedFileDisplayNumber falls back to file_id when no conversation-local number exists', async () => {
  const { resolveUploadedFileDisplayNumber } = await loadFileSelectionUtils()

  assert.equal(typeof resolveUploadedFileDisplayNumber, 'function')
  assert.equal(
    resolveUploadedFileDisplayNumber({ file_id: 157 }),
    157
  )
})
