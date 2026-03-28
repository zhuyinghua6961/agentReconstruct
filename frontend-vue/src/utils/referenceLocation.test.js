import test from 'node:test'
import assert from 'node:assert/strict'

import { resolveLocationBadge, resolveLocationTitle } from './referenceLocation.js'

test('resolveLocationTitle prefers section name', () => {
  assert.equal(resolveLocationTitle({ section: 'Results', chunk_index: 4 }), 'Results')
})

test('resolveLocationTitle falls back to chunk label', () => {
  assert.equal(resolveLocationTitle({ chunk_index: 4 }), '片段 #5')
})

test('resolveLocationBadge falls back to chunk label when only chunk index exists', () => {
  assert.equal(resolveLocationBadge({ chunk_index: 4 }), '片段')
})
