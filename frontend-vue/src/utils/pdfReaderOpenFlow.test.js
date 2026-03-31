import test from 'node:test'
import assert from 'node:assert/strict'

import { buildPdfReaderOpenState, releasePdfBlobUrl } from './pdfReaderOpenFlow.js'

test('buildPdfReaderOpenState returns pdf-ready state and revokes replaced blob url', () => {
  const revoked = []

  const state = buildPdfReaderOpenState({
    doi: '10.1234/demo',
    loadResult: { ok: true, blobUrl: 'blob:new-pdf', contentType: 'application/pdf' },
    previousBlobUrl: 'blob:old-pdf',
    revokeObjectURL: (value) => revoked.push(value),
  })

  assert.deepEqual(revoked, ['blob:old-pdf'])
  assert.equal(state.pdfUrl, 'blob:new-pdf')
  assert.equal(state.pdfError, null)
})

test('buildPdfReaderOpenState returns quota-card error state for quota JSON failures', () => {
  const state = buildPdfReaderOpenState({
    doi: '10.1234/demo',
    loadResult: {
      ok: false,
      errorPayload: {
        code: 'QUOTA_EXCEEDED',
        message: 'quota exceeded',
        data: {
          quota_type: 'file_view',
          quota_name: '查看原文',
          current: 5,
          limit: 5,
          remaining: 0,
          reset_hint: 'next_day_start',
        },
      },
    },
  })

  assert.equal(state.pdfUrl, '')
  assert.equal(state.pdfError.message, 'quota exceeded')
  assert.equal(state.pdfError.quotaCard.featureTitle, '查看原文')
  assert.equal(state.pdfError.quotaCard.variant, 'quota_exceeded')
})

test('buildPdfReaderOpenState keeps non-quota upstream error text for ordinary JSON failures', () => {
  const state = buildPdfReaderOpenState({
    doi: '10.1234/demo',
    loadResult: {
      ok: false,
      errorPayload: {
        status: 500,
        error: 'minio upstream unavailable',
      },
    },
  })

  assert.equal(state.pdfUrl, '')
  assert.equal(state.pdfError.message, 'minio upstream unavailable')
  assert.equal(state.pdfError.quotaCard, null)
})

test('releasePdfBlobUrl revokes existing blob url and ignores plain urls', () => {
  const revoked = []
  releasePdfBlobUrl('blob:active', (value) => revoked.push(value))
  releasePdfBlobUrl('/api/view_pdf/demo', (value) => revoked.push(value))
  assert.deepEqual(revoked, ['blob:active'])
})
