import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const currentDir = dirname(fileURLToPath(import.meta.url))
const source = readFileSync(join(currentDir, 'Home.vue'), 'utf8')

test('Home uses absolute message identity in the render list wiring', () => {
  assert.match(source, /import\s*\{\s*buildVisibleMessageWindow,\s*resolveHiddenHistoryReveal\s*\}\s*from '\.\.\/utils\/messageWindowing'/)
  assert.match(source, /const activeVisibleWindow = computed\(\(\) => \{/)
  assert.match(source, /const visibleMessageEntries = computed\(\(\) => activeVisibleWindow\.value\.visibleMessages\)/)
  assert.match(source, /v-for="entry in visibleMessageEntries"/)
  assert.match(source, /:key="entry\.absoluteMessageIndex"/)
  assert.match(source, /:data-message-index="entry\.absoluteMessageIndex"/)
  assert.match(source, /@click="toggleSteps\(entry\.absoluteMessageIndex\)"/)
})

test('Home imports and uses question anchor helper for stable user-message ids', () => {
  assert.match(
    source,
    /import\s*\{\s*buildQuestionOutlineItems,\s*buildQuestionOutlineSignature,\s*getQuestionAnchorId\s*\}\s*from '\.\.\/utils\/questionOutline'/
  )
  assert.match(source, /:id="entry\.message\.role === 'user' \? getQuestionAnchorId\(entry\.absoluteMessageIndex\) : undefined"/)
  assert.match(source, /:ref="entry\.message\.role === 'user' \? \(el\) => setUserMessageElement\(entry\.absoluteMessageIndex, el\) : null"/)
})

test('Home adds hidden-history reveal flow on top of stable identity rendering', () => {
  assert.match(
    source,
    /import\s*\{\s*buildVisibleMessageWindow,\s*resolveHiddenHistoryReveal\s*\}\s*from '\.\.\/utils\/messageWindowing'/
  )
  assert.match(source, /const MESSAGE_WINDOW_THRESHOLD = 30/)
  assert.match(source, /const DEFAULT_VISIBLE_MESSAGE_COUNT = 24/)
  assert.match(source, /const HISTORY_REVEAL_BATCH_SIZE = 20/)
  assert.match(source, /const revealedHiddenMessageCount = ref\(0\)/)
  assert.match(source, /const hiddenHistoryCount = computed\(\(\) => activeVisibleWindow\.value\.hiddenCount\)/)
  assert.match(source, /function revealHiddenHistory\(targetAbsoluteIndex = null\)/)
  assert.match(source, /v-if="hiddenHistoryCount > 0"/)
  assert.match(source, /@click="revealHiddenHistory\(\)"/)
})

test('Home routes outline jumps through reveal-first flow while preserving stable message identity', () => {
  assert.match(source, /async function scrollToQuestion\(item\)/)
  assert.match(source, /const didReveal = revealHiddenHistory\(item\.messageIndex\)/)
  assert.match(source, /if \(didReveal\) \{\s*await nextTick\(\)\s*\}/)
  assert.match(source, /const target = userMessageElements\.get\(item\.messageIndex\)/)
  assert.match(source, /highlightedQuestionMessageIndex\.value = item\.messageIndex/)
  assert.match(source, /function getMessageByAbsoluteIndex\(messageIndex\)/)
  assert.match(source, /const messageElement = target\.closest\('\.message\[data-message-index\]'\)/)
  assert.match(source, /const currentMsg = getMessageByAbsoluteIndex\(messageIndex\)/)
  assert.match(source, /function toggleSteps\(index\) \{\s*const msg = getMessageByAbsoluteIndex\(index\)/s)
  assert.match(source, /:class="\{ active: highlightedQuestionMessageIndex === item\.messageIndex \}"/)
})
