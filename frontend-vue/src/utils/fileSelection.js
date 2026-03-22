function normalizePositiveInteger(value) {
  const number = Number(value || 0)
  return Number.isInteger(number) && number > 0 ? number : 0
}

function normalizePositiveFileId(value) {
  return normalizePositiveInteger(value)
}

export function mergeSelectedFileIdsAfterUpload(selectedFileIds, uploadedFileId) {
  const merged = []
  const seen = new Set()

  for (const value of Array.isArray(selectedFileIds) ? selectedFileIds : []) {
    const id = normalizePositiveFileId(value)
    if (!id || seen.has(id)) continue
    seen.add(id)
    merged.push(id)
  }

  const nextUploadedFileId = normalizePositiveFileId(uploadedFileId)
  if (nextUploadedFileId && !seen.has(nextUploadedFileId)) {
    merged.push(nextUploadedFileId)
  }

  return merged
}

export function resolveUploadedFileDisplayNumber(documentLike) {
  const file = documentLike && typeof documentLike === 'object' ? documentLike : {}
  return (
    normalizePositiveInteger(file.display_no) ||
    normalizePositiveInteger(file.file_no) ||
    normalizePositiveInteger(file.file_id)
  )
}
