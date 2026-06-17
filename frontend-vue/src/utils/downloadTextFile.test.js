import test from 'node:test'
import assert from 'node:assert/strict'
import { sanitizeDownloadFilename } from './downloadTextFile.js'
import { buildFullDocumentTranslationFilename } from './pdfReaderTranslationDownload.js'

test('sanitizeDownloadFilename replaces unsafe characters and trims', () => {
  assert.equal(sanitizeDownloadFilename('10.1000/example:part'), '10.1000_example_part')
  assert.equal(sanitizeDownloadFilename('  '), 'document')
  assert.equal(sanitizeDownloadFilename('CN123456789A'), 'CN123456789A')
})

test('sanitizeDownloadFilename truncates very long values', () => {
  const longValue = 'a'.repeat(200)
  assert.equal(sanitizeDownloadFilename(longValue).length, 120)
})

test('buildFullDocumentTranslationFilename builds doi and patent names', () => {
  assert.equal(
    buildFullDocumentTranslationFilename({
      documentType: 'doi',
      documentId: '10.1000/example',
    }),
    'doi_10.1000_example_translation.md',
  )
  assert.equal(
    buildFullDocumentTranslationFilename({
      documentType: 'patent',
      documentId: 'CN123456789A',
    }),
    'patent_CN123456789A_translation.md',
  )
})

test('buildFullDocumentTranslationFilename falls back to label', () => {
  assert.equal(
    buildFullDocumentTranslationFilename({
      documentType: 'doi',
      documentId: '',
      label: 'paper-label',
    }),
    'doi_paper-label_translation.md',
  )
})
