import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const currentDir = dirname(fileURLToPath(import.meta.url))
const source = readFileSync(join(currentDir, 'PdfReader.vue'), 'utf8')

test('PdfReader keeps only summary and translation tabs and no stale split-panel logic', () => {
  assert.doesNotMatch(source, /panelMode === 'citations'/)
  assert.match(source, /panelMode === 'summary'/)
  assert.match(source, /panelMode === 'translation'/)

  assert.doesNotMatch(source, /panelMode === 'both'/)
  assert.doesNotMatch(source, /summaryHeight/)
})

test('PdfReader exposes the handlers used by the current template', () => {
  assert.match(source, /function setPanelMode\(mode\)/)
  assert.match(source, /function startResize\(e\)/)
  assert.match(source, /function stopResize\(\)/)
  assert.match(source, /function startTranslationResize\(e\)/)
  assert.match(source, /function stopTranslationResize\(\)/)
})


test('PdfReader no longer renders citations-only panel layout', () => {
  assert.doesNotMatch(source, /:class="\{ 'citations-only': isCitationsVisible \}"/)
  assert.doesNotMatch(source, /\.right-panel\.citations-only \.location-panel\s*\{/)
  assert.doesNotMatch(source, /引用位置/)
})

test('PdfReader uses the shared quota card and single-request pdf open flow', () => {
  assert.match(source, /import QuotaLimitCard from '\.\/QuotaLimitCard\.vue'/)
  assert.match(source, /import \{ fetchPdfDocument, fetchPdfDocumentByUrl \} from '\.\.\/api\/literature'/)
  assert.match(source, /import \{ buildPdfReaderOpenState, releasePdfBlobUrl \} from '\.\.\/utils\/pdfReaderOpenFlow'/)
  assert.match(source, /<QuotaLimitCard v-if="pdfError\?\.quotaCard" :card="pdfError\.quotaCard" \/>/)
})

test('PdfReader translation panel exposes paste-and-translate structure', () => {
  assert.match(source, /translationSubTab === 'snippet'/)
  assert.match(source, /translationSubTab === 'document'/)
  assert.match(source, /@click="setTranslationSubTab\('snippet'\)"/)
  assert.match(source, /@click="setTranslationSubTab\('document'\)"/)
  assert.match(source, /ref="translationBodyRef"/)
  assert.match(source, /class="translation-splitter"/)
  assert.match(source, /@mousedown\.prevent="startTranslationResize"/)
  assert.match(source, /@touchstart\.prevent="startTranslationResize"/)
  assert.match(source, /:style="\{ height: translationInputHeight \+ 'px' \}"/)
  assert.match(source, /粘贴并翻译/)
  assert.match(source, /clipboardFeedback/)
  assert.match(source, /@click="pasteAndTranslate"/)
  assert.match(source, /读取系统剪贴板内容，不是当前 PDF 划选内容/)
  assert.match(source, /\.\.\/utils\/pdfReaderClipboardTranslate\.js'/)
  assert.match(source, /buildTranslatePayload/)
  assert.match(source, /classifyClipboardFailure/)
  assert.match(source, /getClipboardFeedbackMessage/)
  assert.match(source, /normalizeClipboardText/)
  assert.match(source, /const translationSessionId = ref\(0\)/)
  assert.match(source, /function resetTranslationInteractionSession\(\)/)
  assert.match(source, /function isActiveTranslationSession\(sessionId\)/)
  assert.match(source, /const hasManualTranslateText = computed\(\(\) => normalizeClipboardText\(manualText\.value\)\.length > 0\)/)
  assert.match(source, /:disabled="!hasManualTranslateText \|\| isTranslating"/)
  assert.match(source, /async function runTranslation\(text, sessionId = translationSessionId\.value\)/)
  assert.match(source, /async function pasteAndTranslate\(\)[\s\S]*?const sessionId = translationSessionId\.value[\s\S]*?isTranslating\.value = true[\s\S]*?translationQuotaCard\.value = null/)
  assert.match(source, /await clipboardApi\.readText\(\)[\s\S]*?if \(!isActiveTranslationSession\(sessionId\)\) return/)
  assert.match(source, /await api\.translate\(buildTranslatePayload\(text\)\)[\s\S]*?if \(!isActiveTranslationSession\(sessionId\)\) return/)
  assert.match(source, /async function translateFullDocument\(force = false\)/)
  assert.match(source, /await api\.translateDocumentStream\(request\.documentType, request\.documentId, \{/)
  assert.match(source, /onEvent:\s*\(event\)\s*=>/)
  assert.match(source, /const currentPatentId = ref\(''\)/)
  assert.match(source, /const fullDocumentTranslationCacheStatus = ref\(''\)/)
  assert.match(source, /function getFullDocumentTranslationStatusLabel\(\)/)
  assert.match(source, /import MarkdownRenderer from '\.\.\/features\/markdown\/MarkdownRenderer\.vue'/)
  assert.doesNotMatch(source, /createStreamingHtmlRenderer/)
  assert.doesNotMatch(source, /getFullDocumentTranslationHtml/)
  assert.doesNotMatch(source, /fullDocumentTranslationMessage/)
  assert.match(source, /fullDocumentTranslationCacheStatus\.value = String\(event\?\.cache_status \|\| ''\)/)
  assert.match(source, /全文翻译状态：{{ getFullDocumentTranslationStatusLabel\(\) }}/)
  assert.match(source, /<MarkdownRenderer[\s\S]*:content="fullDocumentTranslation"[\s\S]*:streaming="isDocumentTranslating"[\s\S]*variant="document"/)
  assert.doesNotMatch(source, /v-html=/)
  assert.match(source, /function setTranslationSubTab\(mode\)/)
})

test('PdfReader styles the translation panel as a vertically resizable split layout', () => {
  assert.match(source, /\.translation-body\s*\{/)
  assert.match(source, /\.translation-splitter\s*\{/)
  assert.match(source, /cursor:\s*row-resize;/)
  assert.match(source, /\.translation-actions\s*\{[\s\S]*?flex:\s*0 0 auto;/)
})

test('PdfReader clears clipboard feedback when manual translation text changes', () => {
  assert.match(source, /import \{ computed, onBeforeUnmount, ref, watch \} from 'vue'/)
  assert.match(source, /watch\(manualText, \(\) => \{/)
  assert.match(source, /clipboardFeedback\.value = ''/)
})

test('PdfReader resets translate busy state when opening and closing the reader', () => {
  assert.match(source, /async function openReader\(doi, locations = \[\]\) \{[\s\S]*?resetTranslationInteractionSession\(\)[\s\S]*?clipboardFeedback\.value = ''/)
  assert.match(source, /async function openUrlReader\(label, documentUrl, locations = \[\]\) \{[\s\S]*?fetchPdfDocumentByUrl\(documentUrl\)/)
  assert.match(source, /function closeReader\(\) \{[\s\S]*?resetTranslationInteractionSession\(\)[\s\S]*?translationQuotaCard\.value = null[\s\S]*?clipboardFeedback\.value = ''/)
})
