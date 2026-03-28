import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const currentDir = dirname(fileURLToPath(import.meta.url))
const source = readFileSync(join(currentDir, 'PdfReader.vue'), 'utf8')

test('PdfReader keeps only the three target tabs and no stale split-panel logic', () => {
  assert.match(source, /panelMode === 'citations'/)
  assert.match(source, /panelMode === 'summary'/)
  assert.match(source, /panelMode === 'translation'/)

  assert.doesNotMatch(source, /panelMode === 'both'/)
  assert.doesNotMatch(source, /assist-splitter/)
  assert.doesNotMatch(source, /stopVerticalResize\(/)
  assert.doesNotMatch(source, /startVerticalResize/)
  assert.doesNotMatch(source, /summaryHeight/)
})

test('PdfReader exposes the handlers used by the current template', () => {
  assert.match(source, /function setPanelMode\(mode\)/)
  assert.match(source, /function startResize\(e\)/)
  assert.match(source, /function stopResize\(\)/)
})


test('PdfReader citation panel uses the full right-panel space when citations tab is active', () => {
  assert.match(source, /:class="\{ 'citations-only': isCitationsVisible \}"/)
  assert.match(source, /\.right-panel\.citations-only \.location-panel\s*\{/) 
  assert.match(source, /flex:\s*1 1 auto;/)
  assert.match(source, /max-height:\s*none;/)
})
