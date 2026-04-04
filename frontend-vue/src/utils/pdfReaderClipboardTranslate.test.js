import test from 'node:test'
import assert from 'node:assert/strict'

import {
  buildTranslatePayload,
  classifyClipboardFailure,
  getClipboardFeedbackMessage,
  normalizeClipboardText,
} from './pdfReaderClipboardTranslate.js'

test('normalizeClipboardText trims valid clipboard text and rejects whitespace-only content', () => {
  assert.equal(normalizeClipboardText('  copied text  '), 'copied text')
  assert.equal(normalizeClipboardText('\n\t  '), '')
})

test('buildTranslatePayload wraps normalized text into a single-element array', () => {
  assert.deepEqual(buildTranslatePayload('copied text'), ['copied text'])
})

test('classifyClipboardFailure returns unsupported when clipboard api is unavailable', () => {
  const kind = classifyClipboardFailure(
    null,
    { hasNavigator: false, hasClipboardApi: false, hasReadText: false, isSecureContext: false },
  )
  assert.equal(kind, 'unsupported')
})

test('classifyClipboardFailure returns denied when readText throws NotAllowedError', () => {
  const error = new DOMException('Permission denied', 'NotAllowedError')
  const kind = classifyClipboardFailure(
    error,
    { hasNavigator: true, hasClipboardApi: true, hasReadText: true, isSecureContext: true },
  )
  assert.equal(kind, 'denied')
})

test('classifyClipboardFailure falls back to unknown for other exceptions', () => {
  const error = new Error('unexpected clipboard failure')
  const kind = classifyClipboardFailure(
    error,
    { hasNavigator: true, hasClipboardApi: true, hasReadText: true, isSecureContext: true },
  )
  assert.equal(kind, 'unknown')
})

test('getClipboardFeedbackMessage returns the agreed inline copy', () => {
  assert.equal(getClipboardFeedbackMessage('empty'), '剪贴板里没有可翻译的文本')
  assert.equal(getClipboardFeedbackMessage('unsupported'), '当前环境不支持一键读取剪贴板，请手动粘贴')
  assert.equal(getClipboardFeedbackMessage('denied'), '浏览器不允许直接读取剪贴板，请手动粘贴')
  assert.equal(getClipboardFeedbackMessage('unknown'), '读取剪贴板失败，请手动粘贴后再试')
})
