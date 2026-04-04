import test from 'node:test'
import assert from 'node:assert/strict'

import { fetchPdfDocument, fetchPdfDocumentByUrl } from './literature.js'

test('fetchPdfDocument returns blobUrl when view_pdf responds with PDF', async () => {
  const originalFetch = global.fetch
  const originalCreate = URL.createObjectURL
  const calls = []

  global.fetch = async (url, options) => {
    calls.push({ url, options })
    return new Response(new Blob(['pdf-bytes'], { type: 'application/pdf' }), {
      status: 200,
      headers: { 'content-type': 'application/pdf' },
    })
  }
  URL.createObjectURL = () => 'blob:pdf-demo'
  global.localStorage = {
    getItem(key) {
      if (key === 'token') return 'demo-token'
      return ''
    },
  }

  try {
    const result = await fetchPdfDocument('10.1234/demo')
    assert.equal(result.ok, true)
    assert.equal(result.blobUrl, 'blob:pdf-demo')
    assert.match(String(calls[0].url), /\/api\/view_pdf\/10\.1234\/demo/)
    assert.equal(calls[0].options.headers.Authorization, 'Bearer demo-token')
  } finally {
    global.fetch = originalFetch
    URL.createObjectURL = originalCreate
    delete global.localStorage
  }
})

test('fetchPdfDocument returns errorPayload when view_pdf responds with JSON error', async () => {
  const originalFetch = global.fetch

  global.fetch = async () => new Response(
    JSON.stringify({
      success: false,
      code: 'QUOTA_EXCEEDED',
      message: 'quota exceeded',
      data: { quota_type: 'file_view', remaining: 0 },
    }),
    {
      status: 429,
      headers: { 'content-type': 'application/json' },
    }
  )

  try {
    const result = await fetchPdfDocument('10.1234/demo')
    assert.equal(result.ok, false)
    assert.equal(result.errorPayload.code, 'QUOTA_EXCEEDED')
    assert.equal(result.errorPayload.data.quota_type, 'file_view')
  } finally {
    global.fetch = originalFetch
  }
})

test('fetchPdfDocumentByUrl requests the provided patent original url with auth header', async () => {
  const originalFetch = global.fetch
  const originalCreate = URL.createObjectURL
  const calls = []

  global.fetch = async (url, options) => {
    calls.push({ url, options })
    return new Response(new Blob(['pdf-bytes'], { type: 'application/pdf' }), {
      status: 200,
      headers: { 'content-type': 'application/pdf' },
    })
  }
  URL.createObjectURL = () => 'blob:patent-pdf-demo'
  global.localStorage = {
    getItem(key) {
      if (key === 'token') return 'demo-token'
      return ''
    },
  }

  try {
    const result = await fetchPdfDocumentByUrl('/api/patent/original/CN100420075C')
    assert.equal(result.ok, true)
    assert.equal(result.blobUrl, 'blob:patent-pdf-demo')
    assert.match(String(calls[0].url), /\/api\/patent\/original\/CN100420075C$/)
    assert.equal(calls[0].options.headers.Authorization, 'Bearer demo-token')
  } finally {
    global.fetch = originalFetch
    URL.createObjectURL = originalCreate
    delete global.localStorage
  }
})
