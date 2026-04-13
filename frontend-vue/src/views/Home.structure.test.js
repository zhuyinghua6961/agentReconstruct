import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const currentDir = dirname(fileURLToPath(import.meta.url))
const source = readFileSync(join(currentDir, 'Home.vue'), 'utf8')
const scriptSource = source.slice(0, source.indexOf('</script>'))
const assistantMessageSection = source.slice(
  source.indexOf(`<template v-else-if="entry.message.role === 'bot' || entry.message.role === 'assistant'">`),
  source.indexOf(`<div v-else-if="getTaskPhaseLabel(store.currentChatId)" class="loading-animation">`)
)

function assertGraphScopedSelector(selector) {
  assert.match(
    source,
    new RegExp(
      String.raw`(?:\.message-content\.message-graph-kb\s*:deep\(\.graph-kb-markdown ${selector.replace('\\.', '.')}\)|:deep\(\.message-content\.message-graph-kb \.graph-kb-markdown ${selector.replace('\\.', '.')}\))(?=\s*,|\s*\{)`
    )
  )
}

function assertNoGlobalMessageContentSelector(selector) {
  assert.doesNotMatch(
    source,
    new RegExp(
      String.raw`(?:^|,)\s*(?![^,{]*message-graph-kb)[^,{]*\.message-content[^,{]*:deep\(${selector}\)(?=\s*,|\s*\{)`,
      'm'
    )
  )
}

test('Home formats graph_kb query mode as a knowledge-graph badge during streaming', () => {
  assert.match(source, /const ASK_MODE_LABELS = \{ fast: '快速模式', thinking: '思考模式', patent: '专利模式', graph_kb: '知识图谱', neo4j: '知识图谱' \}/)
})

test('Home marks graph kb assistant messages with a graph-only class for labeled and raw graph modes', () => {
  const wrapperMatch = assistantMessageSection.match(
    /<div(?=[^>]*\bmessage-content\b)(?=[^>]*:class="[^"]*message-graph-kb[^"]*")(?=[^>]*:class="[^"]*entry\.message[^"]*")[^>]*:class="([^"]*message-graph-kb[^"]*)"[^>]*>/
  )
  assert.ok(wrapperMatch, 'assistant message-content wrapper should bind message-graph-kb through :class')
  const classExpr = wrapperMatch[1]

  const helperMatch = classExpr.match(/message-graph-kb'\s*:\s*([A-Za-z_][A-Za-z0-9_]*)\(entry\.message\)/)
  if (helperMatch) {
    const helperName = helperMatch[1]
    const helperDefinition = new RegExp(
      String.raw`(?:function\s+${helperName}\s*\(|const\s+${helperName}\s*=\s*(?:\([^)]*\)|[A-Za-z_][A-Za-z0-9_]*)\s*=>)[\s\S]*?(?:知识图谱|graph_kb|neo4j)[\s\S]*?(?:queryMode|query_mode)|(?:function\s+${helperName}\s*\(|const\s+${helperName}\s*=\s*(?:\([^)]*\)|[A-Za-z_][A-Za-z0-9_]*)\s*=>)[\s\S]*?(?:queryMode|query_mode)[\s\S]*?(?:知识图谱|graph_kb|neo4j)`,
      's'
    )
    assert.match(scriptSource, helperDefinition)
    return
  }

  assert.match(classExpr, /知识图谱|queryMode/)
  assert.match(classExpr, /query_mode|graph_kb|neo4j/)
})

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
    /import\s*\{\s*buildQuestionOutlineItems,\s*buildQuestionOutlineSignature,\s*getLastQuestionOutlineItem,\s*getQuestionAnchorId\s*\}\s*from '\.\.\/utils\/questionOutline'/
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
  assert.match(source, /import \{ focusQuestionItem \} from '\.\.\/utils\/questionFocus'/)
  assert.match(source, /async function scrollToQuestion\(item\)/)
  assert.match(source, /await focusQuestionItem\(\{\s*item,\s*userMessageElements,\s*revealHiddenHistory,\s*nextTick,/s)
  assert.match(source, /setActiveQuestionMessageIndex:\s*\(value\)\s*=>\s*\{\s*activeQuestionMessageIndex\.value = value\s*\}/s)
  assert.match(source, /setHighlightedQuestionMessageIndex:\s*\(value\)\s*=>\s*\{\s*highlightedQuestionMessageIndex\.value = value\s*\}/s)
  assert.match(source, /scheduleHighlightReset:\s*scheduleQuestionHighlightReset/)
  assert.match(source, /behavior:\s*'smooth'/)
  assert.match(source, /highlight:\s*true/)
  assert.match(source, /function getMessageByAbsoluteIndex\(messageIndex\)/)
  assert.match(source, /const messageElement = target\.closest\('\.message\[data-message-index\]'\)/)
  assert.match(source, /const currentMsg = getMessageByAbsoluteIndex\(messageIndex\)/)
  assert.match(source, /function toggleSteps\(index\) \{\s*const msg = getMessageByAbsoluteIndex\(index\)/s)
  assert.match(source, /:class="\{ active: activeQuestionMessageIndex === item\.messageIndex \}"/)
})

test('Home restores the current conversation to the newest question when entering or refreshing a chat', () => {
  assert.match(source, /const activeQuestionMessageIndex = ref\(null\)/)
  assert.match(source, /function resetQuestionOutlineState\(\) \{\s*clearQuestionHighlight\(\)\s*activeQuestionMessageIndex\.value = null\s*userMessageElements\.clear\(\)\s*\}/s)
  assert.match(source, /function scheduleQuestionHighlightReset\(\) \{/)
  assert.match(source, /async function focusLastQuestionInView\(options = \{\}\) \{/)
  assert.match(source, /const lastQuestionItem = getLastQuestionOutlineItem\(questionOutlineItems\.value\)/)
  assert.match(source, /activeQuestionMessageIndex\.value = lastQuestionItem\.messageIndex/)
  assert.match(source, /await focusQuestionItem\(\{\s*item:\s*lastQuestionItem,\s*userMessageElements,\s*revealHiddenHistory,\s*nextTick,/s)
  assert.match(source, /behavior:\s*options\?\.behavior \|\| 'auto'/)
  assert.match(source, /highlight:\s*false/)
  assert.match(source, /await focusLastQuestionInView\(\{ behavior: 'auto' \}\)/)
  assert.match(source, /await store\.switchChat\(store\.currentChatId \|\| store\.chats\[0\]\.id\)\s*await focusLastQuestionInView\(\{ behavior: 'auto' \}\)/s)
  assert.match(source, /await store\.switchChat\(nextChatId\)\s*if \(requestSeq !== switchChatRequestSeq\) return\s*await focusLastQuestionInView\(\{ behavior: 'auto' \}\)/s)
  assert.match(source, /:class="\{ active: activeQuestionMessageIndex === item\.messageIndex \}"/)
})

test('Home renders quota limit cards inline for quota failures while keeping markdown fallback', () => {
  assert.match(source, /import QuotaLimitCard from '\.\.\/components\/QuotaLimitCard\.vue'/)
  assert.match(source, /import \{ buildRoutingErrorMarkdown, buildRoutingErrorPresentation, getRouteModeLabel, mergeRoutingMetadata \} from '\.\.\/utils\/routingStatus'/)
  assert.match(source, /function getQuotaCard\(message\)/)
  assert.match(source, /mergedMeta\.quota_card = presentation\.card/)
  assert.match(source, /<QuotaLimitCard v-if="getQuotaCard\(entry\.message\)" :card="getQuotaCard\(entry\.message\)" \/>/)
  assert.match(source, /<QuotaLimitCard v-if="getQuotaCard\(entry\.message\)" :card="getQuotaCard\(entry\.message\)" \/>\s*<div\s+v-else-if="entry\.message\.content && isStreamingTextMessage\(entry\.message\)"[\s\S]*class="message-markdown-content"/s)
  assert.match(source, /<template v-else-if="entry\.message\.content">/)
  assert.match(source, /<div\s+class="message-markdown-content"[\s\S]*v-html="getRenderedMessageHtml\(entry\.message\)"[\s\S]*><\/div>/s)
})

test('Home preserves DOI click routing from rendered markdown into the PDF reader', () => {
  assert.match(source, /if \(target\.classList && target\.classList\.contains\('doi-link'\)\)/)
  assert.match(source, /const doi = target\.getAttribute\('data-doi'\)/)
  assert.match(source, /const messageElement = target\.closest\('\.message\[data-message-index\]'\)/)
  assert.match(source, /const currentMsg = getMessageByAbsoluteIndex\(messageIndex\)/)
  assert.match(source, /const locations = buildCitationLocationsForDoi\(\{/)
  assert.match(source, /pdfReader\.value\.openReader\(doi, locations\)/)
})

test('Home renders failed terminal assistant messages as terminal cards instead of loading placeholders', () => {
  assert.match(source, /function getTerminalMessageState\(message\)/)
  assert.match(source, /function getTerminalMessageTitle\(message\)/)
  assert.match(source, /function getTerminalMessageDetail\(message\)/)
  assert.match(source, /if \(raw === 'failed' \|\| raw === 'canceled' \|\| raw === 'expired'\) return raw/)
  assert.match(source, /if \(state === 'expired'\) return '已结束'/)
  assert.match(source, /if \(state === 'expired'\) return '这次回答已过期结束，请重新发起提问。'/)
  assert.match(source, /<div v-if="getTerminalMessageState\(entry\.message\)" class="terminal-message-inline" :class="'terminal-message-' \+ getTerminalMessageState\(entry\.message\)">/)
  assert.match(source, /<div v-else-if="getTerminalMessageState\(entry\.message\)" class="terminal-message-card"/)
  assert.match(source, /<div class="terminal-message-title">{{ getTerminalMessageTitle\(entry\.message\) }}<\/div>/)
  assert.match(source, /<div v-if="getTerminalMessageDetail\(entry\.message\)" class="terminal-message-detail">{{ getTerminalMessageDetail\(entry\.message\) }}<\/div>/)
  assert.match(source, /\.terminal-message-canceled \{/)
  assert.match(source, /\.terminal-message-expired \{/)
  assert.match(source, /<div v-else class="loading-animation"><span>思考中\.\.\.<\/span><\/div>/)
})

test('Home ignores late stream errors after a done event has already completed the message', () => {
  assert.match(source, /import \{[^}]*shouldIgnoreLateStreamError[^}]*\} from '\.\.\/utils\/streamingLifecycle'/)
  assert.match(source, /streaming_terminal_event:\s*'done'/)
  assert.match(source, /done_seen:\s*true/)
  assert.match(source, /if \(shouldIgnoreLateStreamError\(targetMessage\)\) \{\s*return\s*\}/)
})

test('Home ignores late content frames and drops buffered leftovers after a done event', () => {
  assert.match(source, /import \{ shouldIgnoreLateStreamContent, shouldIgnoreLateStreamError \} from '\.\.\/utils\/streamingLifecycle'/)
  assert.match(source, /if \(data\.type === 'content'\) \{[\s\S]*const targetMessage = getStreamingTargetMessage\(chatId\)\?\.message \|\| \{\}[\s\S]*if \(shouldIgnoreLateStreamContent\(targetMessage\)\) \{\s*activeRuntime\.pendingContent = ''[\s\S]*return \{ terminal: false, skipped: true \}\s*\}/s)
  assert.match(source, /function flushPendingStreamContent\(chatId\) \{[\s\S]*const target = getStreamingTargetMessage\(chatId\)[\s\S]*if \(shouldIgnoreLateStreamContent\(target\?\.message \|\| \{\}\)\) \{\s*runtime\.pendingContent = ''\s*return\s*\}/s)
})

test('Home scopes busy controls to the current chat instead of globally locking the page', () => {
  assert.match(source, /const isCurrentChatBusy = computed\(\(\) => store\.isChatBusy\(store\.currentChatId\)\)/)
  assert.match(source, /const canSend = computed\(\(\) => inputMessage\.value\.trim\(\) && !isCurrentChatBusy\.value\)/)
  assert.match(source, /<button class="new-chat-btn" type="button" @click="createNewChat">新建对话<\/button>/)
  assert.match(source, /<button class="collapsed-new-chat-btn" type="button" @click="createNewChat" title="新建对话">＋<\/button>/)
  assert.match(source, /:disabled="selectedAskMode === option\.value \|\| isCurrentChatBusy"/)
  assert.match(source, /:disabled="uploading \|\| isCurrentChatBusy"/)
  assert.match(source, /<button class="send-btn" :disabled="!canToggleStreaming" @click="sendMessage">{{ isCurrentChatBusy \? '⏹' : '➤' }}<\/button>/)
  assert.doesNotMatch(source, /<button class="new-chat-btn" type="button" :disabled="store\.isStreaming"/)
  assert.doesNotMatch(source, /<button class="collapsed-new-chat-btn" type="button" :disabled="store\.isStreaming"/)
  assert.doesNotMatch(source, /if \(store\.isStreaming\) return\s*\n\s*stopFileStatusPolling\(\)\s*\n\s*clearSelectedFiles\(\)\s*\n\s*resetQuestionOutlineState\(\)\s*\n\s*store\.createChat\(\)/s)
  assert.doesNotMatch(source, /async function switchChat\(chatId\) \{[\s\S]*if \(store\.isStreaming\) return/)
})

test('Home renders per-chat busy badges with sidebar stop and delete guards', () => {
  assert.match(source, /<span v-if="getTaskPhaseLabel\(chat\.id\)" class="history-status-badge">{{ getTaskPhaseLabel\(chat\.id\) }}<\/span>/)
  assert.match(source, /<button\s+v-if="isChatBusy\(chat\.id\)"\s+class="history-stop-btn"/)
  assert.match(source, /@click\.stop="stopStreaming\(chat\.id\)"/)
  assert.match(source, /<button\s+class="history-delete-btn"/)
  assert.match(source, /:disabled="isChatBusy\(chat\.id\)"/)
  assert.match(source, /:title="isChatBusy\(chat\.id\) \? '生成中不可删除' : '删除对话'"/)
})

test('Home keeps current-chat file deletion disabled while that chat is busy', () => {
  assert.match(source, /<button class="pdf-remove-btn" @click\.stop="file\.type === 'pdf' \? handleRemovePdf\(file\.file_id\) : handleRemoveExcel\(file\.file_id\)" :disabled="isCurrentChatBusy" :title="isCurrentChatBusy \? '生成中不可删除文件' : '删除'">×<\/button>/)
})

test('Home stop flow can cancel a chat during dispatch before streaming runtime fully starts', () => {
  assert.match(source, /const messageChat = await store\.addUserMessage\(message, \{ chatId: requestedChatId \}\)/)
  assert.match(source, /if \(!store\.isChatBusy\(streamChatId\) \|\| store\.isChatStopRequested\(streamChatId\)\) return/)
  assert.match(source, /const runtime = createStreamRuntime\(streamChatId, streamRequestId, -1\)/)
  assert.match(source, /if \(runtime\?\.abortController\?\.signal\.aborted \|\| !store\.isChatBusy\(streamChatId\) \|\| store\.isChatStopRequested\(streamChatId\)\) \{\s*return\s*\}/)
  assert.match(source, /store\.addBotMessage\([\s\S]*\{ chatId: streamChatId \}\)/)
})

test('Home snapshots per-chat request context before async chat promotion and reuses it for askStream', () => {
  assert.match(source, /import \{ buildChatRequestContext \} from '\.\.\/utils\/chatRequestContext'/)
  assert.match(source, /const requestChatContext = buildChatRequestContext\(\{\s*chat: requestContextChat,\s*sessionState: store\.sessionState,\s*selectedFileIds: selectedFileIds\.value,\s*\}\)/s)
  assert.match(source, /const messageChat = await store\.addUserMessage\(message, \{ chatId: requestedChatId \}\)/)
  assert.match(source, /const pdfContext = requestChatContext/)
  assert.doesNotMatch(source, /const pdfContext = \{\s*newly_uploaded_ids: store\.getNewlyUploadedFileIds\(\),\s*all_available_ids: store\.getAllUploadedFileIds\(\),\s*selected_ids: \[\.\.\.selectedFileIds\.value\],\s*last_focus_ids: getLastFocusFileIds\(\),\s*last_turn_route: getLastTurnRoute\(\)\s*\}/s)
})

test('Home routes refresh-survivable sends through task create and recovery instead of legacy ask_stream', () => {
  assert.match(source, /import \{ createRecoverableTaskController \} from '\.\.\/utils\/recoverableTaskController'/)
  assert.match(source, /const recoverableTaskController = createRecoverableTaskController\(\{/)
  assert.match(source, /if \(store\.refreshSurvivableQATasksEnabled\) \{\s*return sendTaskMessage\(\)\s*\}/)
  assert.match(source, /async function sendTaskMessage\(\)/)
  assert.match(source, /const result = await recoverableTaskController\.sendTaskMessage\(\{/)
  assert.doesNotMatch(source, /for await \(const data of api\.askStream\(/)
})

test('Home exposes queued and admitted task states in the UI instead of showing every recoverable task as streaming', () => {
  assert.match(source, /function getTaskPhaseLabel\(chatId\)/)
  assert.match(source, /if \(taskStatus === 'queued'\) return '排队中'/)
  assert.match(source, /if \(taskStatus === 'admitted'\) return '即将开始'/)
  assert.match(source, /if \(taskStatus === 'running'\) return '生成中'/)
  assert.match(source, /<span v-if="getTaskPhaseLabel\(chat\.id\)" class="history-status-badge">{{ getTaskPhaseLabel\(chat\.id\) }}<\/span>/)
  assert.match(source, /<div v-else-if="getTaskPhaseLabel\(store\.currentChatId\)" class="loading-animation"><span>{{ getTaskPhaseLabel\(store\.currentChatId\) }}\.\.\.<\/span><\/div>/)
})

test('Home stop flow uses gateway task cancel for recoverable tasks and attaches recovery on current-chat active_task', () => {
  assert.match(source, /const currentRecoverableTaskSnapshot = computed\(\(\) => \{\s*const chatId = normalizeChatId\(store\.currentChatId\)\s*const chat = getChatById\(chatId\)\s*const activeTask = chat\?\.activeTask/s)
  assert.match(source, /watch\(\s*\(\) => \[\s*currentRecoverableTaskSnapshot\.value\.chatId,\s*currentRecoverableTaskSnapshot\.value\.taskId,\s*currentRecoverableTaskSnapshot\.value\.status,\s*currentRecoverableTaskSnapshot\.value\.replayAvailable \? '1' : '0',\s*\]/s)
  assert.match(source, /const existingRuntime = getStreamRuntime\(chatId\)/)
  assert.match(source, /if \(existingRuntime\?\.mode === 'task' && existingRuntime\?\.requestId === cursor\.taskId && !existingRuntime\?\.abortController\?\.signal\?\.aborted\) \{\s*taskRecoveryDebug\.log\('home:attach-watch-skip-active-runtime'/s)
  assert.match(source, /const taskSummary = getChatById\(chatId\)\?\.activeTask/)
  assert.match(source, /void attachRecoverableTask\(\{\s*chatId,\s*taskSummary,\s*\}\)/s)
  assert.match(source, /const activeTaskId = String\(chat\?\.activeTask\?\.task_id \|\| ''\)\.trim\(\)/)
  assert.match(source, /if \(store\.refreshSurvivableQATasksEnabled && activeTaskId\) \{\s*void cancelRecoverableTask\(targetChatId, activeTaskId\)\s*return\s*\}/s)
  assert.match(source, /return recoverableTaskController\.cancelRecoverableTask\(chatId, taskId\)/)
  assert.match(source, /recoverableTaskController\.detachAllRecoverableTasks\(\)/)
})

test('Home schedules truth-refresh persistence and force-flushes it on unmount', () => {
  assert.match(source, /async function refreshConversationTruth\(chatId\) \{[\s\S]*taskRecoveryDebug\.log\('home:refresh-conversation-truth'[\s\S]*store\.scheduleTaskRecoveryPersist\(\)\s*return detail\s*\}/s)
  assert.doesNotMatch(source, /async function refreshConversationTruth\(chatId\) \{[\s\S]*taskRecoveryDebug\.log\('home:refresh-conversation-truth'[\s\S]*store\.persistLocalState\(\)\s*return detail\s*\}/s)
  assert.match(source, /onUnmounted\(\(\) => \{\s*store\.flushTaskRecoveryPersist\(\)\s*recoverableTaskController\.detachAllRecoverableTasks\(\)/s)
})

test('Home drops duplicate recoverable task events whose seq is not ahead of the local replay cursor', () => {
  assert.match(source, /function applyGatewayEvent\(chatId, data, runtime = getStreamRuntime\(chatId\)\)/)
  assert.match(source, /const eventSeq = Number\(data\.seq \|\| 0\) \|\| 0/)
  assert.match(source, /const localLastSeq = store\.getChatLastTaskSeq\(chatId\)/)
  assert.match(source, /if \(eventSeq > 0 && eventSeq <= localLastSeq\) \{/)
  assert.match(source, /taskRecoveryDebug\.log\('home:event-skipped-duplicate'/)
})

test('Home flushes buffered recoverable content before settling canceled or expired terminal state events', () => {
  assert.match(source, /if \(status === 'canceled' \|\| status === 'expired'\) \{\s*flushPendingStreamContent\(chatId\)/s)
  assert.match(source, /streaming_terminal_event:\s*status/)
  assert.match(source, /finalizeRecoverableTaskLocally\(chatId, \{ lastSeq: data\.seq \}\)/)
})

test('Home invalidates streaming and final render caches when a message flips into terminal markdown rendering', () => {
  assert.match(
    source,
    /const renderStreamingMessageHtml = createStreamingHtmlRenderer\(\{\s*terminalFormatter:\s*\(text,\s*message\)\s*=>\s*formatAnswer\(text,\s*Array\.isArray\(message\?\.referenceLinks\) \? message\.referenceLinks : \[\]\)\s*\}\)/s
  )
  assert.match(source, /const isComplete = msg\?\.isComplete === true/)
  assert.match(source, /const doneSeen = Boolean\(msg\?\.doneSeen \?\? msg\?\.done_seen \?\? msg\?\.metadata\?\.done_seen\)/)
  assert.match(
    source,
    /const terminalStatus = String\(\s*msg\?\.terminalStatus\s*\?\?\s*msg\?\.terminal_status\s*\?\?\s*msg\?\.status\s*\?\?\s*msg\?\.metadata\?\.terminal_status\s*\?\?\s*msg\?\.metadata\?\.status\s*\?\?\s*msg\?\.metadata\?\.streaming_terminal_event\s*\?\?\s*''\s*\)\.trim\(\)\.toLowerCase\(\)/s
  )
  assert.match(source, /cached\.isComplete === isComplete/)
  assert.match(source, /cached\.doneSeen === doneSeen/)
  assert.match(source, /cached\.terminalStatus === terminalStatus/)
  assert.match(source, /renderedMessageCache\.set\(msg, \{ content, referenceLinks, isComplete, doneSeen, terminalStatus, html \}\)/)
})

test('Home header no longer renders knowledge-base summary status text', () => {
  assert.match(source, /<h1>磷酸铁锂知识图谱 AI<\/h1>/)
  assert.doesNotMatch(source, /const kbSummaryText = computed\(\(\) => \{/)
  assert.doesNotMatch(source, /<div class="kb-info">\{\{ kbSummaryText \}\}<\/div>/)
  assert.doesNotMatch(source, /\.kb-info\s*\{/)
})

test('Home scopes graph kb markdown styles through deep selectors instead of global markdown rules', () => {
  assert.match(source, /class="message-markdown-content"[^>]*:class="\{ 'graph-kb-markdown': isGraphKbMessage\(entry\.message\) \}"/)
  assert.match(source, /class="message-markdown-content"[^>]*:class="\{ 'graph-kb-markdown': isGraphKbMessage\(entry\.message\) \}"[^>]*v-html="getStreamingMessageHtml\(entry\.message\)"/)
  assert.match(source, /class="message-markdown-content"[^>]*:class="\{ 'graph-kb-markdown': isGraphKbMessage\(entry\.message\) \}"[^>]*v-html="getRenderedMessageHtml\(entry\.message\)"/)
  assertGraphScopedSelector('h2')
  assertGraphScopedSelector('h3')
  assertGraphScopedSelector('ul')
  assertGraphScopedSelector('li')
  assertGraphScopedSelector('\\.doi-link')
  assertNoGlobalMessageContentSelector('h2')
  assertNoGlobalMessageContentSelector('h3')
  assertNoGlobalMessageContentSelector('ul')
  assertNoGlobalMessageContentSelector('li')
  assertNoGlobalMessageContentSelector('\\.doi-link')
})
