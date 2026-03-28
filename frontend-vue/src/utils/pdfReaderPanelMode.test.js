import test from 'node:test'
import assert from 'node:assert/strict'

import { isPdfReaderPanelActive, resolvePdfReaderInitialPanelMode } from './pdfReaderPanelMode.js'

test('defaults to citations panel when citation hints exist', () => {
  assert.equal(resolvePdfReaderInitialPanelMode([{ page: 3 }]), 'citations')
})

test('defaults to summary panel when no citation hints exist', () => {
  assert.equal(resolvePdfReaderInitialPanelMode([]), 'summary')
  assert.equal(resolvePdfReaderInitialPanelMode(null), 'summary')
})

test('panel visibility is exact-match by tab key', () => {
  assert.equal(isPdfReaderPanelActive('citations', 'citations'), true)
  assert.equal(isPdfReaderPanelActive('citations', 'summary'), false)
  assert.equal(isPdfReaderPanelActive('translation', 'translation'), true)
})
