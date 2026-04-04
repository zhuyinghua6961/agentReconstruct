import test from 'node:test'
import assert from 'node:assert/strict'

function installMinimalDocumentStub() {
  if (globalThis.document?.createElement) return
  globalThis.document = {
    createElement() {
      return {
        _text: '',
        set textContent(value) {
          this._text = String(value ?? '')
        },
        get innerHTML() {
          return this._text
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;')
        },
      }
    },
  }
}

async function loadRenderUtils() {
  installMinimalDocumentStub()
  return await import('./index.js')
}

test('streaming answer keeps completed doi clickable', async () => {
  const { formatStreamingAnswer } = await loadRenderUtils()

  const html = formatStreamingAnswer('结论成立 (doi=10.1088/1742-6596_2584_1_012046)')

  assert.match(html, /class="doi-link"/)
  assert.match(html, /data-doi="10\.1088\/1742-6596_2584_1_012046"/)
})

test('streaming answer keeps bracket doi clickable', async () => {
  const { formatStreamingAnswer } = await loadRenderUtils()

  const html = formatStreamingAnswer('结论成立 (doi=10.1016/S0378-7753(03)00297-0)')

  assert.match(html, /class="doi-link"/)
  assert.match(html, /data-doi="10\.1016\/S0378-7753\(03\)00297-0"/)
})

test('streaming answer keeps patent citation clickable', async () => {
  const { formatStreamingAnswer } = await loadRenderUtils()

  const html = formatStreamingAnswer('结论成立 (patent_id=CN100420075C)')

  assert.match(html, /class="doi-link patent-link"/)
  assert.match(html, /data-patent-id="CN100420075C"/)
})

test('streaming answer handles incomplete doi fragments without pathological slowdown', async () => {
  const { formatStreamingAnswer } = await loadRenderUtils()

  const prefix = '这是正文。\\n'.repeat(40)
  const start = performance.now()
  let html = ''

  for (const fragment of [
    '(doi=10.1088/1742-6596_2584_1',
    '(doi=10.1088/1742-6596_2584_1_0120',
    '(doi=10.1088/1742-6596_2584_1_012046',
  ]) {
    html = formatStreamingAnswer(`${prefix}${fragment}`)
  }

  const elapsedMs = performance.now() - start

  assert.equal(/class="doi-link"/.test(html), false)
  assert.ok(
    elapsedMs < 250,
    `expected incomplete DOI rendering to stay fast, got ${elapsedMs.toFixed(2)}ms`,
  )
})
