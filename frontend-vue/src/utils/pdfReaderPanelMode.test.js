import test from 'node:test'
import assert from 'node:assert/strict'

import { isPdfReaderPanelActive, resolvePdfReaderInitialPanelMode } from './pdfReaderPanelMode.js'

test('defaults to summary panel even when citation hints exist', () => {
  assert.equal(resolvePdfReaderInitialPanelMode([{ page: 3 }]), 'summary')
})

test('defaults to summary panel when no citation hints exist', () => {
  assert.equal(resolvePdfReaderInitialPanelMode([]), 'summary')
  assert.equal(resolvePdfReaderInitialPanelMode(null), 'summary')
})

test('panel visibility is exact-match by tab key', () => {
  assert.equal(isPdfReaderPanelActive('summary', 'summary'), true)
  assert.equal(isPdfReaderPanelActive('summary', 'translation'), false)
  assert.equal(isPdfReaderPanelActive('translation', 'translation'), true)
})
