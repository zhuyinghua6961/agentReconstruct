import { sanitizeDownloadFilename } from './downloadTextFile.js'

export function buildFullDocumentTranslationFilename({ documentType, documentId, label }) {
  const base = sanitizeDownloadFilename(documentId || label || 'document')
  const prefix = documentType === 'patent' ? 'patent' : 'doi'
  return `${prefix}_${base}_translation.md`
}
