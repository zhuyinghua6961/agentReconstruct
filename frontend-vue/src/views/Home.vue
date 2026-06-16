<script setup>
import { ref, onMounted, onUnmounted, computed, nextTick, watch } from 'vue'
import { useChatStore } from '../stores/chatStore'
import { api } from '../services/api'
import { formatTime } from '../utils'
import { buildMessageRenderMemoKey } from '../utils/messageRenderMemo'
import { buildVisibleMessageWindow, resolveHiddenHistoryReveal } from '../utils/messageWindowing'
import { resolveStreamingTarget } from '../utils/streamingTarget'
import { buildChatRequestContext } from '../utils/chatRequestContext'
import { focusQuestionItem } from '../utils/questionFocus'
import { buildQuestionOutlineItems, buildQuestionOutlineSignature, getLastQuestionOutlineItem, getQuestionAnchorId } from '../utils/questionOutline'
import { getMessageStageTimingModel, getStepTimingDurationLabel as getStepTimingDurationLabelFromModel } from '../utils/stageTimings'
import { DEFAULT_NEAR_BOTTOM_THRESHOLD_PX, isNearBottom, shouldAutoScroll } from '../utils/scrollFollow'
import { mergeSelectedFileIdsAfterUpload, resolveUploadedFileDisplayNumber } from '../utils/fileSelection'
import {
  buildPendingDraftFile,
  buildPendingDraftFileItem,
  defaultPromptForDraftFileType,
  resolveDraftFileType,
} from '../utils/draftFiles'
import { shouldIgnoreLateStreamContent, shouldIgnoreLateStreamError } from '../utils/streamingLifecycle'
import { normalizeTaskReplayCursor } from '../utils/taskReplayCursor'
import { consumePendingStreamContent, shouldClearRecoveredActiveTask } from '../utils/taskRecoveryRuntime'
import { createTaskRecoveryDebugLogger, summarizeTaskRecoveryDetail } from '../utils/taskRecoveryDebug'
import { createRecoverableTaskController } from '../utils/recoverableTaskController'
import { formatQueryModeLabel, resolveActualQueryModeLabel } from '../utils/queryMode'
import {
  buildPatentStreamingMessagePatch,
  getPatentPreviewStreams,
  isPatentFinalAnswerPending,
  reducePatentStreamingState,
} from '../utils/patentStreaming'
import PdfReader from '../components/PdfReader.vue'
import QuotaLimitCard from '../components/QuotaLimitCard.vue'
import MarkdownRenderer from '../features/markdown/MarkdownRenderer.vue'
import { buildCitationLocationsForDoi } from '../utils/citationEvidence'
import { buildRoutingErrorPresentation, mergeRoutingMetadata } from '../utils/routingStatus'

const PINNED_CHATS_COLLAPSED_KEY = 'lfp.sidebar.pinned-collapsed.v1'
const RECENT_CHATS_COLLAPSED_KEY = 'lfp.sidebar.recent-collapsed.v1'
const FILE_LIST_COLLAPSED_KEY = 'lfp.file-list.collapsed.v1'
const ASK_MODE_STORAGE_KEY = 'gateway.ask.mode.v1'

const store = useChatStore()
const pdfReader = ref(null)
const appContainer = ref(null)
const inputMessage = ref('')
const messagesArea = ref(null)
const fileInput = ref(null)
const uploading = ref(false)
const uploadProgress = ref(0)
const streamRuntimeByChatId = new Map()
const taskAttachInFlightByChatId = new Map()
const selectedFileIds = ref([])
const pendingDraftFiles = ref([])
const selectedAskMode = ref(localStorage.getItem(ASK_MODE_STORAGE_KEY) || 'thinking')
const leftSidebarCollapsed = ref(false)
const leftSidebarWidth = ref(280)
const leftSidebarLastExpandedWidth = ref(280)
let fileStatusPollTimer = null
let scrollFrame = null
const FILE_POLL_BASE_MS = 2000
const FILE_POLL_MAX_MS = 30000
const FILE_POLL_MAX_FAILURES = 6
let filePollBackoffMs = FILE_POLL_BASE_MS
let filePollConsecutiveFailures = 0
let filePollConsecutiveUnchanged = 0
let filePollLastPendingSignature = ''
let filePollInFlight = false
const questionOutlineCollapsed = ref(false)
const questionOutlineWidth = ref(300)
const questionOutlineLastExpandedWidth = ref(300)
const activeQuestionMessageIndex = ref(null)
const highlightedQuestionMessageIndex = ref(null)
const userMessageElements = new Map()
const isPanelResizing = ref(false)
let questionHighlightTimer = null
let activeResizePanel = null
let switchChatRequestSeq = 0

const LEFT_SIDEBAR_MIN_WIDTH = 220
const LEFT_SIDEBAR_MAX_WIDTH = 420
const LEFT_SIDEBAR_COLLAPSED_WIDTH = 88
const RIGHT_PANEL_MIN_WIDTH = 220
const RIGHT_PANEL_MAX_WIDTH = 420
const RIGHT_PANEL_COLLAPSED_WIDTH = 92
const MAIN_CHAT_MIN_WIDTH = 520
const PANEL_SPLITTER_WIDTH = 10
const PANEL_SPLITTER_TOTAL_WIDTH = PANEL_SPLITTER_WIDTH * 2
const MESSAGE_WINDOW_THRESHOLD = 30
const DEFAULT_VISIBLE_MESSAGE_COUNT = 24
const HISTORY_REVEAL_BATCH_SIZE = 20

const hasMessages = computed(() => store.currentMessages.length > 0)
const isCurrentChatBusy = computed(() => store.isChatBusy(store.currentChatId))
const canSend = computed(() => inputMessage.value.trim() && !isCurrentChatBusy.value)
const canToggleStreaming = computed(() => {
  if (isCurrentChatBusy.value) return true
  return Boolean(canSend.value)
})
const askModeOptions = [
  { value: 'fast', label: '文献' },
  { value: 'thinking', label: '深度' },
  { value: 'patent', label: '专利' }
]
const pinnedChatsCollapsed = ref(false)
const recentChatsCollapsed = ref(false)
const fileListCollapsed = ref(false)
const currentLeftSidebarWidth = computed(() =>
  leftSidebarCollapsed.value ? LEFT_SIDEBAR_COLLAPSED_WIDTH : leftSidebarWidth.value
)
const currentRightPanelWidth = computed(() =>
  questionOutlineCollapsed.value ? RIGHT_PANEL_COLLAPSED_WIDTH : questionOutlineWidth.value
)
const pinnedChats = computed(() => store.chats.filter(chat => Boolean(chat?.isPinned)))
const recentChats = computed(() => store.chats.filter(chat => !Boolean(chat?.isPinned)))
const questionOutlineItems = ref([])
const revealedHiddenMessageCount = ref(0)
const activeVisibleWindow = computed(() => {
  const totalMessages = store.currentMessages.length
  const shouldWindow = totalMessages > MESSAGE_WINDOW_THRESHOLD
  return buildVisibleMessageWindow({
    messages: store.currentMessages,
    visibleCount: shouldWindow ? DEFAULT_VISIBLE_MESSAGE_COUNT : totalMessages,
    revealedCount: shouldWindow ? revealedHiddenMessageCount.value : 0,
  })
})
const visibleMessageEntries = computed(() => activeVisibleWindow.value.visibleMessages)
const hiddenHistoryCount = computed(() => activeVisibleWindow.value.hiddenCount)
const currentRecoverableTaskSnapshot = computed(() => {
  const chatId = normalizeChatId(store.currentChatId)
  const chat = getChatById(chatId)
  const activeTask = chat?.activeTask && typeof chat.activeTask === 'object' ? chat.activeTask : null
  return {
    chatId,
    taskId: String(activeTask?.task_id || '').trim(),
    status: String(activeTask?.status || '').trim().toLowerCase(),
    replayAvailable: activeTask?.replay_available !== false,
  }
})
const questionOutlineSignature = computed(() => buildQuestionOutlineSignature(store.currentMessages))
const isNearBottomRef = ref(true)
const pendingAutoScroll = ref(false)

const taskRecoveryDebug = createTaskRecoveryDebugLogger()

function normalizeAskMode(mode) {
  const value = String(mode || 'thinking').trim().toLowerCase()
  return ['fast', 'thinking', 'patent'].includes(value) ? value : 'thinking'
}

function normalizeChatId(chatId) {
  return String(chatId ?? '').trim()
}

function getChatById(chatId) {
  const normalizedChatId = normalizeChatId(chatId)
  if (!normalizedChatId) return null
  return store.chats.find(chat => normalizeChatId(chat?.id) === normalizedChatId) || null
}

function getChatSyncStatus(chat) {
  const status = String(chat?.syncStatus || '').trim().toLowerCase()
  if (['syncing', 'failed', 'synced', 'local'].includes(status)) return status
  if (chat?.synced) return 'synced'
  return 'local'
}

function getStreamRuntime(chatId) {
  const normalizedChatId = normalizeChatId(chatId)
  if (!normalizedChatId) return null
  return streamRuntimeByChatId.get(normalizedChatId) || null
}

function createStreamRuntime(chatId, requestId, targetIndex = -1, options = {}) {
  const normalizedChatId = normalizeChatId(chatId)
  if (!normalizedChatId) return null

  const runtime = {
    chatId: normalizedChatId,
    requestId: String(requestId || '').trim(),
    targetIndex: Number.isInteger(targetIndex) ? targetIndex : -1,
    abortController: new AbortController(),
    pendingContent: '',
    flushFrame: null,
    strictRequestMatch: options?.strictRequestMatch ?? Boolean(requestId),
    mode: String(options?.mode || 'legacy').trim().toLowerCase() || 'legacy',
    pollTimer: null,
    eventState: {
      thinkingIndex: 0,
      activeStepKey: '',
    },
  }
  streamRuntimeByChatId.set(normalizedChatId, runtime)
  return runtime
}

function resetStreamFlushState(chatId) {
  const runtime = getStreamRuntime(chatId)
  if (!runtime) return
  runtime.pendingContent = ''
  if (runtime.flushFrame !== null) {
    window.cancelAnimationFrame(runtime.flushFrame)
    runtime.flushFrame = null
  }
}

function clearStreamRuntime(chatId) {
  const runtime = getStreamRuntime(chatId)
  if (!runtime) return
  resetStreamFlushState(chatId)
  if (runtime.pollTimer) {
    window.clearTimeout(runtime.pollTimer)
    runtime.pollTimer = null
  }
  streamRuntimeByChatId.delete(runtime.chatId)
}

function getStreamingTargetMessage(chatId) {
  const runtime = getStreamRuntime(chatId)
  const chat = getChatById(runtime?.chatId)
  if (!chat || !Array.isArray(chat.messages) || chat.messages.length === 0) return null

  const target = resolveStreamingTarget({
    messages: chat.messages,
    requestId: runtime?.requestId,
    cachedTargetIndex: runtime?.targetIndex,
    strictRequestMatch: Boolean(runtime?.strictRequestMatch),
  })
  if (!target) {
    runtime.targetIndex = -1
    return null
  }

  runtime.targetIndex = target.index
  return { chat, message: target.message, index: target.index, runtime }
}

function updateStreamingTargetMessage(chatId, updates) {
  const target = getStreamingTargetMessage(chatId)
  if (!target?.message) return null

  if (updates.references !== undefined) {
    target.message.references = Array.isArray(updates.references) ? [...updates.references] : []
  }

  if (updates.referenceLinks !== undefined) {
    target.message.referenceLinks = Array.isArray(updates.referenceLinks) ? [...updates.referenceLinks] : []
  }

  Object.keys(updates).forEach((key) => {
    if (key !== 'references' && key !== 'referenceLinks') {
      target.message[key] = updates[key]
    }
  })

  return target.message
}

function isChatBusy(chatId) {
  return store.isChatBusy(chatId)
}

function isStreamingChat(chatId) {
  return store.isChatStreaming(chatId)
}

function isHistoryItemDisabled(chatId) {
  return false
}

function getHistoryItemTitle(chatId) {
  if (isChatBusy(chatId)) {
    return '当前会话正在生成回答'
  }
  return ''
}

function sleepWithSignal(ms, signal) {
  return new Promise((resolve) => {
    if (signal?.aborted) {
      resolve()
      return
    }
    const timer = window.setTimeout(() => {
      cleanup()
      resolve()
    }, Math.max(0, Number(ms || 0)))
    const cleanup = () => {
      window.clearTimeout(timer)
      signal?.removeEventListener?.('abort', onAbort)
    }
    const onAbort = () => {
      cleanup()
      resolve()
    }
    signal?.addEventListener?.('abort', onAbort, { once: true })
  })
}

function getTaskPhaseLabel(chatId) {
  const chat = getChatById(chatId)
  const taskStatus = String(
    chat?.activeTask?.status
    || store.getChatBusyRuntime(chatId)?.phase
    || ''
  ).trim().toLowerCase()
  if (taskStatus === 'queued') return '排队中'
  if (taskStatus === 'admitted') return '即将开始'
  if (taskStatus === 'running') return '生成中'
  if (taskStatus === 'streaming' || taskStatus === 'dispatching') return '生成中'
  return ''
}

async function refreshConversationTruth(chatId) {
  const targetChatId = normalizeChatId(chatId)
  const chat = getChatById(targetChatId)
  if (!chat?.synced) return null

  const uid = store.getUserId()
  if (!uid) return null

  const detail = await api.getConversationDetail(parseInt(chat.id), uid)
  const liveChat = getChatById(targetChatId)
  if (!liveChat) return detail

  liveChat.title = detail.title || liveChat.title
  liveChat.messages = Array.isArray(detail.messages) ? [...detail.messages] : []
  liveChat.messageCount = Number(detail.message_count || liveChat.messages.length)
  liveChat.updatedAt = detail.updated_at || liveChat.updatedAt
  if (Array.isArray(detail.pdf_list)) {
    liveChat.pdf_list = [...detail.pdf_list]
  }
  if (Array.isArray(detail.excel_list)) {
    liveChat.excel_list = [...detail.excel_list]
  }
  if (Array.isArray(detail.uploaded_files)) {
    liveChat.uploaded_files = [...detail.uploaded_files]
  }
  if (detail.active_task) {
    store.setChatActiveTask(targetChatId, detail.active_task, { touch: false, persist: false })
  } else {
    store.clearChatActiveTask(targetChatId, { touch: false, persist: false })
  }
  taskRecoveryDebug.log('home:refresh-conversation-truth', {
    chatId: targetChatId,
    detail: summarizeTaskRecoveryDetail(
      detail,
      detail?.active_task?.task_id || liveChat?.activeTask?.task_id || '',
    ),
    localLastSeq: store.getChatLastTaskSeq(targetChatId),
  })
  store.scheduleTaskRecoveryPersist()
  return detail
}

async function refreshConversationTruthFallback(chatId, fallbackLastSeq = 0) {
  const detail = await refreshConversationTruth(chatId)
  const shouldClear = shouldClearRecoveredActiveTask(detail, fallbackLastSeq)
  if (!shouldClear) {
    const cursor = normalizeTaskReplayCursor(detail?.active_task, Math.max(store.getChatLastTaskSeq(chatId), fallbackLastSeq))
    return {
      detail,
      keepRecovering: true,
      cursor,
    }
  }
  finalizeRecoverableTaskLocally(chatId, { lastSeq: fallbackLastSeq, clearActiveTask: true })
  return {
    detail,
    keepRecovering: false,
    cursor: normalizeTaskReplayCursor({}, fallbackLastSeq),
  }
}

const recoverableTaskController = createRecoverableTaskController({
  api,
  store,
  normalizeChatId,
  getChatById,
  getCurrentChatId: () => store.currentChatId,
  taskAttachInFlightByChatId,
  streamRuntimeByChatId,
  getStreamRuntime,
  createStreamRuntime,
  clearStreamRuntime,
  refreshConversationTruth,
  refreshConversationTruthFallback,
  applyGatewayEvent,
  sleepWithSignal,
  clearInput: () => {
    inputMessage.value = ''
  },
  scrollToBottom,
  onError: (scope, error) => {
    console.error(`[${scope}]`, error)
  },
  debugLog: (scope, payload) => {
    taskRecoveryDebug.log(scope, payload)
  },
})

function setAskMode(mode) {
  selectedAskMode.value = normalizeAskMode(mode)
  localStorage.setItem(ASK_MODE_STORAGE_KEY, selectedAskMode.value)
}

function getActualQueryModeLabel(data = {}, metadata = {}, options = {}) {
  return resolveActualQueryModeLabel(data, metadata, options)
}

function isGraphKbMessage(message) {
  const labeledMode = formatQueryModeLabel(message?.queryMode || '')
  if (labeledMode === '知识图谱') return true

  const rawMode = String(
    message?.metadata?.query_mode
    || message?.query_mode
    || message?.metadata?.queryMode
    || message?.queryMode
    || ''
  ).trim().toLowerCase()

  return ['graph_kb', 'neo4j'].includes(rawMode)
}

function isPatentMessage(message) {
  const candidates = [
    message?.metadata?.actual_mode,
    message?.actual_mode,
    message?.metadata?.requested_mode,
    message?.requested_mode,
    message?.metadata?.query_mode,
    message?.query_mode,
    message?.metadata?.queryMode,
    message?.queryMode,
    message?.metadata?.route,
    message?.route,
  ]
    .map((value) => String(value || '').trim().toLowerCase())
    .filter(Boolean)

  return candidates.some((value) => value === 'patent' || value.startsWith('patent_'))
}

function getMessageMarkdownVariant(message) {
  if (isGraphKbMessage(message)) return 'graph-kb'
  if (isPatentMessage(message)) return 'patent-message'
  return 'message'
}

function setUserMessageElement(messageIndex, el) {
  if (el) {
    userMessageElements.set(messageIndex, el)
    return
  }
  userMessageElements.delete(messageIndex)
}

function getMessageByAbsoluteIndex(messageIndex) {
  return Number.isInteger(messageIndex) && messageIndex >= 0
    ? store.currentMessages[messageIndex] || null
    : null
}

function getQuotaCard(message) {
  const card = message?.metadata?.quota_card
  return card && typeof card === 'object' ? card : null
}

function getTerminalMessageState(message) {
  const raw = String(
    message?.terminalStatus
    || message?.status
    || message?.metadata?.terminal_status
    || message?.metadata?.status
    || ''
  ).trim().toLowerCase()
  if (raw === 'failed' || raw === 'canceled' || raw === 'expired') return raw
  return ''
}

function getTerminalMessageTitle(message) {
  const state = getTerminalMessageState(message)
  if (state === 'failed') return '处理失败'
  if (state === 'canceled') return '已取消'
  if (state === 'expired') return '已结束'
  return ''
}

function getTerminalMessageDetail(message) {
  const failureMessage = String(
    message?.failureMessage
    || message?.metadata?.failure_message
    || ''
  ).trim()
  if (failureMessage) return failureMessage
  const state = getTerminalMessageState(message)
  if (state === 'failed') return '这次回答没有成功完成，你可以稍后重试。'
  if (state === 'canceled') return '这次回答已结束，没有继续生成。'
  if (state === 'expired') return '这次回答已过期结束，请重新发起提问。'
  return ''
}

function clearQuestionHighlight() {
  highlightedQuestionMessageIndex.value = null
  if (questionHighlightTimer !== null) {
    window.clearTimeout(questionHighlightTimer)
    questionHighlightTimer = null
  }
}

function scheduleQuestionHighlightReset() {
  if (questionHighlightTimer !== null) {
    window.clearTimeout(questionHighlightTimer)
  }
  questionHighlightTimer = window.setTimeout(() => {
    highlightedQuestionMessageIndex.value = null
    questionHighlightTimer = null
  }, 1800)
}

function resetQuestionOutlineState() {
  clearQuestionHighlight()
  activeQuestionMessageIndex.value = null
  userMessageElements.clear()
}

function resetHiddenHistoryState() {
  revealedHiddenMessageCount.value = 0
}

function clampValue(value, min, max) {
  return Math.min(Math.max(value, min), max)
}

function getContainerWidth() {
  return appContainer.value?.getBoundingClientRect().width || window.innerWidth
}

function getMaxLeftSidebarWidth() {
  const remaining = getContainerWidth() - currentRightPanelWidth.value - PANEL_SPLITTER_TOTAL_WIDTH - MAIN_CHAT_MIN_WIDTH
  return Math.max(LEFT_SIDEBAR_MIN_WIDTH, Math.min(LEFT_SIDEBAR_MAX_WIDTH, remaining))
}

function getMaxRightPanelWidth() {
  const remaining = getContainerWidth() - currentLeftSidebarWidth.value - PANEL_SPLITTER_TOTAL_WIDTH - MAIN_CHAT_MIN_WIDTH
  return Math.max(RIGHT_PANEL_MIN_WIDTH, Math.min(RIGHT_PANEL_MAX_WIDTH, remaining))
}

function clampPanelWidths() {
  if (!leftSidebarCollapsed.value) {
    leftSidebarWidth.value = clampValue(leftSidebarWidth.value, LEFT_SIDEBAR_MIN_WIDTH, getMaxLeftSidebarWidth())
    leftSidebarLastExpandedWidth.value = leftSidebarWidth.value
  }
  if (!questionOutlineCollapsed.value) {
    questionOutlineWidth.value = clampValue(questionOutlineWidth.value, RIGHT_PANEL_MIN_WIDTH, getMaxRightPanelWidth())
    questionOutlineLastExpandedWidth.value = questionOutlineWidth.value
  }
  if (!leftSidebarCollapsed.value) {
    leftSidebarWidth.value = clampValue(leftSidebarWidth.value, LEFT_SIDEBAR_MIN_WIDTH, getMaxLeftSidebarWidth())
    leftSidebarLastExpandedWidth.value = leftSidebarWidth.value
  }
}

function setLeftSidebarCollapsed(collapsed) {
  if (collapsed) {
    if (!leftSidebarCollapsed.value) {
      leftSidebarLastExpandedWidth.value = leftSidebarWidth.value
    }
    leftSidebarCollapsed.value = true
    clampPanelWidths()
    return
  }
  leftSidebarCollapsed.value = false
  leftSidebarWidth.value = clampValue(
    leftSidebarLastExpandedWidth.value || 280,
    LEFT_SIDEBAR_MIN_WIDTH,
    getMaxLeftSidebarWidth()
  )
  clampPanelWidths()
}

function toggleLeftSidebar() {
  setLeftSidebarCollapsed(!leftSidebarCollapsed.value)
}

function setQuestionOutlinePanelCollapsed(collapsed) {
  if (collapsed) {
    if (!questionOutlineCollapsed.value) {
      questionOutlineLastExpandedWidth.value = questionOutlineWidth.value
    }
    questionOutlineCollapsed.value = true
    clampPanelWidths()
    return
  }
  questionOutlineCollapsed.value = false
  questionOutlineWidth.value = clampValue(
    questionOutlineLastExpandedWidth.value || 300,
    RIGHT_PANEL_MIN_WIDTH,
    getMaxRightPanelWidth()
  )
  clampPanelWidths()
}

function toggleQuestionOutline() {
  setQuestionOutlinePanelCollapsed(!questionOutlineCollapsed.value)
}

function stopPanelResize() {
  activeResizePanel = null
  isPanelResizing.value = false
  window.removeEventListener('mousemove', handlePanelResize)
  window.removeEventListener('mouseup', stopPanelResize)
}

function handlePanelResize(event) {
  if (!activeResizePanel || !appContainer.value) return
  const rect = appContainer.value.getBoundingClientRect()
  if (activeResizePanel === 'left') {
    const nextWidth = event.clientX - rect.left
    leftSidebarWidth.value = clampValue(nextWidth, LEFT_SIDEBAR_MIN_WIDTH, getMaxLeftSidebarWidth())
    leftSidebarLastExpandedWidth.value = leftSidebarWidth.value
    return
  }
  if (activeResizePanel === 'right') {
    const nextWidth = rect.right - event.clientX
    questionOutlineWidth.value = clampValue(nextWidth, RIGHT_PANEL_MIN_WIDTH, getMaxRightPanelWidth())
    questionOutlineLastExpandedWidth.value = questionOutlineWidth.value
  }
}

function startPanelResize(panel, event) {
  if (window.innerWidth <= 1024) return
  event.preventDefault()
  if (panel === 'left' && leftSidebarCollapsed.value) {
    setLeftSidebarCollapsed(false)
  }
  if (panel === 'right' && questionOutlineCollapsed.value) {
    setQuestionOutlinePanelCollapsed(false)
  }
  activeResizePanel = panel
  isPanelResizing.value = true
  window.addEventListener('mousemove', handlePanelResize)
  window.addEventListener('mouseup', stopPanelResize)
}

async function scrollToQuestion(item) {
  if (!item) return
  await focusQuestionItem({
    item,
    userMessageElements,
    revealHiddenHistory,
    nextTick,
    setActiveQuestionMessageIndex: (value) => {
      activeQuestionMessageIndex.value = value
    },
    setHighlightedQuestionMessageIndex: (value) => {
      highlightedQuestionMessageIndex.value = value
    },
    scheduleHighlightReset: scheduleQuestionHighlightReset,
    behavior: 'smooth',
    highlight: true,
  })
}

async function focusLastQuestionInView(options = {}) {
  updateQuestionOutlineItems()
  const lastQuestionItem = getLastQuestionOutlineItem(questionOutlineItems.value)
  if (!lastQuestionItem) {
    activeQuestionMessageIndex.value = null
    return
  }
  activeQuestionMessageIndex.value = lastQuestionItem.messageIndex
  if (options?.scroll === false) {
    return
  }
  await focusQuestionItem({
    item: lastQuestionItem,
    userMessageElements,
    revealHiddenHistory,
    nextTick,
    setActiveQuestionMessageIndex: (value) => {
      activeQuestionMessageIndex.value = value
    },
    setHighlightedQuestionMessageIndex: (value) => {
      highlightedQuestionMessageIndex.value = value
    },
    scheduleHighlightReset: scheduleQuestionHighlightReset,
    behavior: options?.behavior || 'auto',
    highlight: false,
  })
  clearQuestionHighlight()
}

function isStepsCollapsed(msg) {
  return msg?.stepsCollapsed === true
}

function toggleSteps(index) {
  const msg = getMessageByAbsoluteIndex(index)
  if (!msg) return
  msg.stepsCollapsed = !isStepsCollapsed(msg)
}

function getMessageSteps(msg) {
  return Array.isArray(msg?.steps) ? msg.steps : []
}

function isVisibleProcessStep(step) {
  return String(step?.step || '').trim() !== 'context_ready'
}

function getVisibleMessageSteps(msg) {
  return getMessageSteps(msg).filter(isVisibleProcessStep)
}

function getStepPanelCount(msg) {
  return getVisibleMessageSteps(msg).length
}

function getStageTimingModel(msg) {
  return getMessageStageTimingModel(msg)
}

function getStageTimingSummary(msg) {
  const model = getStageTimingModel(msg)
  if (!model.hasTimings) return ''
  if (model.slowest) {
    return `总耗时 ${model.totalLabel} · 最慢 ${model.slowest.label} ${model.slowest.durationLabel}`
  }
  return `总耗时 ${model.totalLabel}`
}

function hasProcessPanel(msg) {
  return getStepPanelCount(msg) > 0 || getStageTimingModel(msg).hasTimings
}

function getLastStep(msg) {
  const steps = getVisibleMessageSteps(msg)
  if (steps.length === 0) return null
  return steps[steps.length - 1]
}

function splitStepMessage(rawMessage) {
  const message = String(rawMessage || '').trim()
  if (!message) {
    return {
      title: '处理中',
      detail: '',
      stageKey: ''
    }
  }

  const cleaned = message.replace(/^[^\p{L}\p{N}#]+/u, '').trim()
  const compact = cleaned.replace(/\s+/g, ' ')
  const stageMatch = compact.match(/^(阶段[0-9一二三四五六七八九十百千万点\.]+)(?:\s*[：:]\s*|\s+)(.+)$/u)
  if (stageMatch) {
    return {
      title: stageMatch[1].trim(),
      detail: stageMatch[2].trim(),
      stageKey: stageMatch[1].replace(/\s+/g, '')
    }
  }

  const sentenceParts = compact.split(/[：:]/)
  if (sentenceParts.length >= 2) {
    const title = sentenceParts[0].trim()
    const detail = sentenceParts.slice(1).join('：').trim()
    if (title && detail) {
      return {
        title,
        detail,
        stageKey: title.length <= 18 ? title.replace(/\s+/g, '') : ''
      }
    }
  }

  if (compact.length <= 18) {
    return {
      title: compact,
      detail: '',
      stageKey: compact.replace(/\s+/g, '')
    }
  }

  return {
    title: compact.slice(0, 18),
    detail: compact,
    stageKey: ''
  }
}

function buildStepPayload(data, fallbackKey, fallbackStatus = 'processing') {
  const explicitStep = String(data?.step || '').trim()
  const message = String(data?.message || data?.content || explicitStep || fallbackKey).trim()
  const normalized = splitStepMessage(message)
  const stableStep = explicitStep || normalized.stageKey || fallbackKey
  const payload = {
    step: stableStep,
    message,
    title: normalized.title,
    detail: normalized.detail,
    status: normalizeStepStatus(data?.status, fallbackStatus),
    data: data?.data && typeof data.data === 'object' ? data.data : undefined,
    sourceType: String(data?.type || 'step'),
    updatedAt: new Date().toISOString()
  }
  if (data?.error) payload.error = String(data.error)
  return payload
}

function getStepIcon(step) {
  const status = normalizeStepStatus(step?.status)
  if (status === 'success') return '●'
  if (status === 'skipped') return '●'
  if (status === 'error') return '●'
  return '●'
}

function getStepTitle(step) {
  if (!step) return '处理中'
  return String(step.title || splitStepMessage(step.message).title || step.message || '处理中')
}

function getStepDetail(step) {
  if (!step) return ''
  if (isGraphPipelineStep(step)) return getGraphStepDetail(step)
  const detail = String(step.detail || '').trim()
  if (detail) return detail
  const title = getStepTitle(step)
  const message = String(step.message || '').trim()
  if (!message || message === title) return ''
  return message
}

function getStepTimingDurationLabel(msg, step) {
  return getStepTimingDurationLabelFromModel(msg, {
    ...step,
    title: getStepTitle(step),
    message: step?.message,
  })
}

function isGraphPipelineStep(step) {
  return String(step?.step || '').trim().startsWith('graph_')
}

function getStepCount(step) {
  const count = Number(step?.data?.count)
  return Number.isFinite(count) && count > 0 ? count : null
}

function getVisibleStepCount(step) {
  if (isGraphPipelineStep(step)) return null
  return getStepCount(step)
}

function getGraphStepDetail(step) {
  if (!step) return ''
  const status = normalizeStepStatus(step?.status)
  const stepKey = String(step?.step || '').trim()
  if (status === 'error') {
    return '图谱检索失败，已自动降级为常规生成链路'
  }
  if (stepKey === 'graph_answer') {
    return '已整理图谱结果并生成回答'
  }
  const mode = String(step?.data?.mode || '').trim()
  if (mode === 'direct_answer') {
    return '已命中结构化答案'
  }
  if (mode === 'skip_graph') {
    return '未找到可直接用于增强生成的图谱线索'
  }
  return '已获取结构化线索，继续文献检索与生成'
}

function getStepOverview(msg) {
  const steps = getVisibleMessageSteps(msg)
  if (steps.length === 0) return ''
  const processing = steps.filter((step) => normalizeStepStatus(step?.status) === 'processing').length
  const success = steps.filter((step) => normalizeStepStatus(step?.status) === 'success').length
  const skipped = steps.filter((step) => normalizeStepStatus(step?.status) === 'skipped').length
  const error = steps.filter((step) => normalizeStepStatus(step?.status) === 'error').length
  if (error > 0) return `失败 ${error} · 完成 ${success}${skipped ? ` · 跳过 ${skipped}` : ''}`
  if (processing > 0) return `进行中 ${processing} · 完成 ${success}${skipped ? ` · 跳过 ${skipped}` : ''}`
  return skipped > 0 ? `已完成 ${success} · 跳过 ${skipped}` : `已完成 ${success}`
}

function getCollapsedStepSummary(msg) {
  const current = [...getVisibleMessageSteps(msg)].reverse().find((step) => normalizeStepStatus(step?.status) === 'processing')
  return current || getLastStep(msg)
}

function upsertStreamingStep(chatId, stepPayload, activeStepKey, { markPreviousActiveSuccess = false } = {}) {
  return updateStreamingSteps(chatId, (steps) => {
    const nextKey = String(stepPayload?.step || '').trim()
    if (markPreviousActiveSuccess && activeStepKey && nextKey && activeStepKey !== nextKey) {
      const activeIdx = steps.findIndex((step) => step.step === activeStepKey)
      if (activeIdx >= 0 && normalizeStepStatus(steps[activeIdx].status) === 'processing') {
        steps[activeIdx] = { ...steps[activeIdx], status: 'success' }
      }
    }

    const existingIdx = steps.findIndex((step) => step.step === nextKey)
    if (existingIdx >= 0) {
      const existing = steps[existingIdx]
      steps[existingIdx] = {
        ...existing,
        ...stepPayload,
        title: stepPayload.title || existing.title,
        detail: stepPayload.detail || existing.detail,
        updatedAt: stepPayload.updatedAt || existing.updatedAt,
      }
      return
    }

    steps.push(stepPayload)
  })
}

function normalizeStepStatus(status, fallback = 'processing') {
  const raw = String(status || '').trim().toLowerCase()
  if (['processing', 'in_progress', 'running', 'pending'].includes(raw)) return 'processing'
  if (['skipped', 'skip', 'skipping'].includes(raw)) return 'skipped'
  if (['success', 'succeeded', 'completed', 'complete', 'done', 'ok'].includes(raw)) return 'success'
  if (['error', 'failed', 'fail', 'failure'].includes(raw)) return 'error'
  return fallback
}

function updateStreamingSteps(chatId, mutator) {
  const target = getStreamingTargetMessage(chatId)
  if (!target?.message) return []
  const steps = Array.isArray(target.message.steps) ? [...target.message.steps] : []
  mutator(steps)
  updateStreamingTargetMessage(chatId, { steps })
  return steps
}

function ensureRuntimeEventState(runtime) {
  if (!runtime) {
    return {
      thinkingIndex: 0,
      activeStepKey: '',
    }
  }
  if (!runtime.eventState || typeof runtime.eventState !== 'object') {
    runtime.eventState = {
      thinkingIndex: 0,
      activeStepKey: '',
    }
  }
  return runtime.eventState
}

function markRuntimeActiveStep(chatId, runtime, status, error = '') {
  const eventState = ensureRuntimeEventState(runtime)
  if (!eventState.activeStepKey) return
  updateStreamingSteps(chatId, (steps) => {
    const idx = steps.findIndex((step) => step.step === eventState.activeStepKey)
    if (idx < 0) return
    steps[idx] = {
      ...steps[idx],
      status: normalizeStepStatus(status, steps[idx].status || 'processing'),
      ...(error ? { error: String(error) } : {}),
      updatedAt: new Date().toISOString()
    }
  })
}

function syncRecoverableTaskSummary(chatId, payload = {}) {
  const targetChatId = normalizeChatId(chatId)
  const chat = getChatById(targetChatId)
  if (!chat) return null
  const existingTask = chat?.activeTask && typeof chat.activeTask === 'object' ? chat.activeTask : {}
  const taskId = String(payload?.task_id || existingTask?.task_id || '').trim()
  if (!taskId) return null
  return store.setChatActiveTask(
    targetChatId,
    {
      ...existingTask,
      ...payload,
      task_id: taskId,
      last_seq: Number(payload?.seq ?? payload?.last_seq ?? chat?.lastTaskSeq ?? 0) || 0,
      replay_available: payload?.replay_available ?? existingTask?.replay_available ?? true,
    },
    { persist: false }
  )
}

function finalizeRecoverableTaskLocally(chatId, options = {}) {
  if (options?.lastSeq !== undefined) {
    store.updateChatTaskReplayCursor(chatId, options.lastSeq, { persist: false, touch: false })
  }
  clearStreamRuntime(chatId)
  store.finishChatBusyRuntime(chatId)
  if (options?.clearActiveTask !== false) {
    store.clearChatActiveTask(chatId, { persist: false, touch: false })
  }
  store.flushTaskRecoveryPersist()
}

function applyGatewayEvent(chatId, data, runtime = getStreamRuntime(chatId)) {
  const eventState = ensureRuntimeEventState(runtime)
  const eventSeq = Number(data.seq || 0) || 0
  const localLastSeq = store.getChatLastTaskSeq(chatId)
  if (eventSeq > 0 && eventSeq <= localLastSeq) {
    taskRecoveryDebug.log('home:event-skipped-duplicate', {
      chatId,
      taskId: String(runtime?.requestId || ''),
      seq: eventSeq,
      localLastSeq,
      type: String(data.type || '').trim().toLowerCase(),
    })
    return { terminal: false, skipped: true }
  }

  if (data.type === 'state') {
    const status = String(data.status || '').trim().toLowerCase()
    if (status === 'queued' || status === 'admitted' || status === 'running') {
      syncRecoverableTaskSummary(chatId, data)
      store.updateChatTaskReplayCursor(chatId, data.seq, { persist: false, touch: false })
      store.scheduleTaskRecoveryPersist()
      return { terminal: false }
    }
    if (status === 'canceled' || status === 'expired') {
      flushPendingStreamContent(chatId)
      const targetMessage = getStreamingTargetMessage(chatId)?.message || {}
      const existingMeta = (targetMessage.metadata && typeof targetMessage.metadata === 'object') ? targetMessage.metadata : {}
      const detailMessage = status === 'expired'
        ? '这次回答已过期结束，请重新发起提问。'
        : '用户已停止生成'
      updateStreamingTargetMessage(chatId, {
        terminalStatus: status,
        status,
        failureMessage: detailMessage,
        failureCode: status === 'expired' ? 'TASK_EXPIRED' : 'ASK_CANCELLED',
        retriable: false,
        doneSeen: false,
        metadata: {
          ...existingMeta,
          terminal_status: status,
          status,
          failure_message: detailMessage,
          failure_code: status === 'expired' ? 'TASK_EXPIRED' : 'ASK_CANCELLED',
          retriable: false,
          done_seen: false,
          streaming_terminal_event: status,
        },
        isComplete: true
      })
      finalizeRecoverableTaskLocally(chatId, { lastSeq: data.seq })
      return { terminal: true, status }
    }
  }

  if (data.type === 'thinking') {
    flushPendingStreamContent(chatId)
    const thinkingMessage = String(data.content || data.message || '').trim()
    if (thinkingMessage) {
      eventState.thinkingIndex += 1
      const stepPayload = buildStepPayload(data, `thinking_${eventState.thinkingIndex}`, 'processing')
      upsertStreamingStep(chatId, stepPayload, eventState.activeStepKey, { markPreviousActiveSuccess: true })
      eventState.activeStepKey = stepPayload.step
    }
    return { terminal: false }
  }

  if (data.type === 'step') {
    flushPendingStreamContent(chatId)
    const stepPayload = buildStepPayload(data, `step_${Date.now()}`, 'processing')
    upsertStreamingStep(chatId, stepPayload, eventState.activeStepKey, {
      markPreviousActiveSuccess:
        stepPayload.step !== eventState.activeStepKey && normalizeStepStatus(stepPayload.status) === 'processing'
    })
    eventState.activeStepKey = stepPayload.step
    return { terminal: false }
  }

  if (data.type === 'metadata') {
    const targetMessage = getStreamingTargetMessage(chatId)?.message || {}
    const existingMeta = (targetMessage.metadata && typeof targetMessage.metadata === 'object') ? targetMessage.metadata : {}
    const mergedMeta = mergeRoutingMetadata(existingMeta, data)
    const mergedTimings = (mergedMeta.timings && typeof mergedMeta.timings === 'object' && !Array.isArray(mergedMeta.timings))
      ? { ...mergedMeta.timings }
      : null
    const actualQueryMode = getActualQueryModeLabel(data, mergedMeta, { allowRouteFallback: false })
    updateStreamingTargetMessage(chatId, {
      expert: data.expert,
      ...(actualQueryMode ? { queryMode: actualQueryMode } : {}),
      metadata: mergedMeta,
      ...(mergedTimings ? { timings: mergedTimings } : {}),
    })
    return { terminal: false }
  }

  if (data.type === 'content') {
    const activeRuntime = getStreamRuntime(chatId)
    if (!activeRuntime) return { terminal: false }
    const targetMessage = getStreamingTargetMessage(chatId)?.message || {}
    if (shouldIgnoreLateStreamContent(targetMessage)) {
      activeRuntime.pendingContent = ''
      taskRecoveryDebug.log('home:event-content-ignored-after-done', {
        chatId,
        taskId: String(activeRuntime?.requestId || ''),
        seq: Number(data.seq || 0) || 0,
        deltaLength: String(data.content || data.delta || '').length,
      })
      return { terminal: false, skipped: true }
    }
    const patentStreamingUpdate = reducePatentStreamingState(targetMessage, data)
    if (patentStreamingUpdate.handled) {
      updateStreamingTargetMessage(chatId, buildPatentStreamingMessagePatch(targetMessage, patentStreamingUpdate.state))
      if (patentStreamingUpdate.mainContentMode === 'preview') {
        if (normalizeChatId(store.currentChatId) === normalizeChatId(chatId)) {
          scrollToBottom()
        }
        return { terminal: false }
      }
      if (patentStreamingUpdate.replaceContent) {
        activeRuntime.pendingContent = ''
        updateStreamingTargetMessage(chatId, { content: '' })
      }
      activeRuntime.pendingContent += patentStreamingUpdate.content
    } else {
      activeRuntime.pendingContent += String(data.content || data.delta || '')
    }
    taskRecoveryDebug.log('home:event-content', {
      chatId,
      taskId: String(activeRuntime?.requestId || ''),
      seq: Number(data.seq || 0) || 0,
      deltaLength: String(patentStreamingUpdate.handled ? patentStreamingUpdate.content : (data.content || data.delta || '')).length,
      pendingLength: String(activeRuntime.pendingContent || '').length,
      localLastSeq: store.getChatLastTaskSeq(chatId),
    })
    scheduleStreamContentFlush(chatId)
    return { terminal: false }
  }

  if (data.type === 'done') {
    flushPendingStreamContent(chatId)
    const targetMessage = getStreamingTargetMessage(chatId)?.message || {}
    const existingMeta = (targetMessage.metadata && typeof targetMessage.metadata === 'object') ? targetMessage.metadata : {}
    const doneMeta = (data.metadata && typeof data.metadata === 'object') ? data.metadata : {}
    const referenceLinks = data.reference_links || data.pdf_links || data.referenceLinks || data.pdfLinks || []
    const references = Array.isArray(data.reference_objects)
      ? data.reference_objects
      : (Array.isArray(data.references) ? data.references : [])
    const mergedMeta = mergeRoutingMetadata({ ...existingMeta, ...doneMeta }, data)
    const finalizedSteps = updateStreamingSteps(chatId, (steps) => {
      if (eventState.activeStepKey) {
        const activeIdx = steps.findIndex((step) => step.step === eventState.activeStepKey)
        if (activeIdx >= 0 && normalizeStepStatus(steps[activeIdx].status) === 'processing') {
          steps[activeIdx] = { ...steps[activeIdx], status: 'success', updatedAt: new Date().toISOString() }
        }
      }
      steps.forEach((step, idx) => {
        if (normalizeStepStatus(step.status) === 'processing') {
          steps[idx] = { ...step, status: 'success', updatedAt: new Date().toISOString() }
        }
      })
    })
    const updates = {
      references,
      referenceLinks,
      steps: finalizedSteps,
      isComplete: true
    }
    if (data.final_answer) updates.content = data.final_answer
    if (data.doi_locations) updates.doiLocations = data.doi_locations
    updates.metadata = {
      ...mergedMeta,
      done_seen: true,
      streaming_terminal_event: 'done',
      used_files: Array.isArray(data.used_files) ? data.used_files : (existingMeta.used_files || []),
      timings: (data.timings && typeof data.timings === 'object') ? data.timings : (existingMeta.timings || {}),
    }
    if (updates.metadata.timings && typeof updates.metadata.timings === 'object' && !Array.isArray(updates.metadata.timings)) {
      updates.timings = { ...updates.metadata.timings }
    }
    updates.queryMode = getActualQueryModeLabel(data, mergedMeta, { allowRouteFallback: false }) || targetMessage.queryMode || ''

    updateStreamingTargetMessage(chatId, updates)
    taskRecoveryDebug.log('home:event-done', {
      chatId,
      taskId: String(getStreamRuntime(chatId)?.requestId || ''),
      seq: Number(data.seq || 0) || 0,
      finalAnswerLength: String(data.final_answer || '').length,
      localLastSeq: store.getChatLastTaskSeq(chatId),
      targetContentLength: String(updates.content || targetMessage.content || '').length,
    })
    return { terminal: true, status: 'completed' }
  }

  if (data.type === 'error') {
    flushPendingStreamContent(chatId)
    const targetMessage = getStreamingTargetMessage(chatId)?.message || {}
    if (shouldIgnoreLateStreamError(targetMessage)) {
      return { terminal: false }
    }
    const existingMeta = (targetMessage.metadata && typeof targetMessage.metadata === 'object') ? targetMessage.metadata : {}
    const mergedMeta = mergeRoutingMetadata(existingMeta, data)
    const errorText = String(data.message || data.error || '处理失败')
    const presentation = buildRoutingErrorPresentation({
      code: data.code,
      message: errorText,
      metadata: mergedMeta,
      data: data.data,
    })
    if (presentation.kind === 'quota_card' && presentation.card) {
      mergedMeta.quota_card = presentation.card
    } else {
      delete mergedMeta.quota_card
    }
    const renderedError = presentation.kind === 'markdown'
      ? presentation.markdown
      : errorText
    if (eventState.activeStepKey) {
      markRuntimeActiveStep(chatId, runtime, 'error', errorText)
    } else {
      updateStreamingSteps(chatId, (steps) => {
        steps.push({
          step: 'error',
          title: '处理失败',
          message: errorText,
          detail: '',
          status: 'error',
          error: errorText,
          updatedAt: new Date().toISOString()
        })
      })
    }
    const existingContent = String(targetMessage.content || '').trim()
    updateStreamingTargetMessage(chatId, {
      content: existingContent ? `${existingContent}\n\n${renderedError}` : renderedError,
      queryMode: getActualQueryModeLabel(data, mergedMeta, { allowRouteFallback: false }) || targetMessage.queryMode || '',
      metadata: {
        ...mergedMeta,
        streaming_terminal_event: 'error',
      },
      isComplete: true
    })
    taskRecoveryDebug.log('home:event-error', {
      chatId,
      taskId: String(getStreamRuntime(chatId)?.requestId || ''),
      seq: Number(data.seq || 0) || 0,
      message: errorText,
      code: String(data.code || ''),
      localLastSeq: store.getChatLastTaskSeq(chatId),
    })
    return { terminal: true, status: 'failed' }
  }

  return { terminal: false }
}

function isStreamingTextMessage(msg) {
  if (!msg) return false
  if (!(msg.role === 'bot' || msg.role === 'assistant')) return false
  return isCurrentChatBusy.value && msg.isComplete !== true
}

function getPatentPreviewSourceLabel(stream) {
  const source = String(stream?.contentSource || '').trim().toLowerCase()
  if (source === 'pdf') return 'PDF 预览'
  if (source === 'table') return '表格预览'
  if (source === 'kb') return '知识库预览'
  if (source === 'hybrid') return '混合预览'
  return '处理中证据'
}

function getMessageRenderMemoKey(msg) {
  return buildMessageRenderMemoKey(msg)
}

function handleMarkdownPatentOpen(patentId) {
  const normalizedPatentId = String(patentId || '').trim()
  if (!normalizedPatentId || !pdfReader.value) return
  pdfReader.value.openUrlReader(
    normalizedPatentId,
    `/api/patent/original/${encodeURIComponent(normalizedPatentId)}`,
    []
  )
}

function handleMarkdownDoiOpen(doi, messageIndex = -1) {
  const normalizedDoi = String(doi || '').trim()
  if (!normalizedDoi || !pdfReader.value) return
  const currentMsg = getMessageByAbsoluteIndex(Number(messageIndex))
  const locations = buildCitationLocationsForDoi({
    doi: normalizedDoi,
    doiLocations: currentMsg?.doiLocations || {},
    references: currentMsg?.references || [],
  })
  pdfReader.value.openReader(normalizedDoi, locations)
}

function flushPendingStreamContent(chatId) {
  const runtime = getStreamRuntime(chatId)
  if (!runtime?.pendingContent) return
  const target = getStreamingTargetMessage(chatId)
  if (shouldIgnoreLateStreamContent(target?.message || {})) {
    runtime.pendingContent = ''
    return
  }
  const existingContent = String(target?.message?.content || '')
  const { nextContent, remainingPending } = consumePendingStreamContent({
    existingContent,
    pendingContent: runtime.pendingContent,
    targetFound: Boolean(target?.message),
  })
  if (target?.message && nextContent !== existingContent) {
    updateStreamingTargetMessage(chatId, { content: nextContent })
  }
  runtime.pendingContent = remainingPending
  if (normalizeChatId(store.currentChatId) === normalizeChatId(chatId)) {
    scrollToBottom()
  }
}

function scheduleStreamContentFlush(chatId) {
  const runtime = getStreamRuntime(chatId)
  if (!runtime || runtime.flushFrame !== null) return
  runtime.flushFrame = window.requestAnimationFrame(() => {
    runtime.flushFrame = null
    flushPendingStreamContent(chatId)
  })
}

// 合并PDF和Excel文件，按file_id统一排序
const mergedAndSortedFiles = computed(() => {
  const files = []
  
  // 添加PDF文件
  if (store.currentChat?.pdf_list) {
    store.currentChat.pdf_list.forEach(pdf => {
      files.push({
        type: 'pdf',
        id: pdf.file_id,
        file_id: pdf.file_id,
        file_no: Number(pdf.file_no || 0),
        display_no: Number(pdf.display_no || 0),
        title: pdf.pdf_title,
        pdf_path: pdf.pdf_path,  // 添加pdf_path用于访问
        parse_status: pdf.parse_status || 'uploaded',
        index_status: pdf.index_status || 'pending',
        processing_stage: pdf.processing_stage || 'uploaded',
        status_updated_at: pdf.status_updated_at || pdf.uploaded_at || new Date().toISOString(),
        last_error: pdf.last_error || '',
        file_meta: pdf.file_meta || {}
      })
    })
  }
  
  // 添加Excel文件
  if (store.currentChat?.excel_list) {
    store.currentChat.excel_list.forEach(excel => {
      files.push({
        type: 'excel',
        id: excel.file_id,
        file_id: excel.file_id,
        file_no: Number(excel.file_no || 0),
        display_no: Number(excel.display_no || 0),
        title: excel.excel_title,
        parse_status: excel.parse_status || 'uploaded',
        index_status: excel.index_status || 'pending',
        processing_stage: excel.processing_stage || 'uploaded',
        status_updated_at: excel.status_updated_at || excel.uploaded_at || new Date().toISOString(),
        last_error: excel.last_error || '',
        file_meta: excel.file_meta || {}
      })
    })
  }
  
  // 优先按会话内展示编号排序（回退到 file_no / file_id）
  return files.sort((a, b) => {
    const aNo = Number(a.display_no || a.file_no || 0)
    const bNo = Number(b.display_no || b.file_no || 0)
    if (aNo > 0 && bNo > 0) return aNo - bNo
    if (aNo > 0) return -1
    if (bNo > 0) return 1
    return Number(a.file_id || 0) - Number(b.file_id || 0)
  })
})

const visibleConversationFiles = computed(() => {
  const uploadedFiles = mergedAndSortedFiles.value
  const draftFiles = pendingDraftFiles.value.map((draft, index) =>
    buildPendingDraftFileItem(draft, uploadedFiles.length + index)
  )
  return [...uploadedFiles, ...draftFiles]
})

const visibleConversationFileCount = computed(() => visibleConversationFiles.value.length)
const hasVisibleConversationFiles = computed(() => visibleConversationFileCount.value > 0)
const hasSelectableConversationFiles = computed(() => mergedAndSortedFiles.value.length > 0)

function isDraftFileItem(file) {
  return String(file?.type || '').startsWith('draft-')
}

function getFileIcon(file) {
  const type = String(file?.type || '').toLowerCase()
  return type.includes('pdf') ? '📄' : '📊'
}

function removePendingDraftFile(draftId) {
  const targetDraftId = String(draftId || '').trim()
  if (!targetDraftId) return
  pendingDraftFiles.value = pendingDraftFiles.value.filter((draft) => draft.draftId !== targetDraftId)
}

function normalizeSelectedFileIds() {
  const currentIds = mergedAndSortedFiles.value
    .map(item => Number(item?.file_id || 0))
    .filter(id => id > 0)
  const currentSet = new Set(currentIds)
  selectedFileIds.value = selectedFileIds.value.filter(id => currentSet.has(Number(id || 0)))
}

function isFileSelected(fileId) {
  const id = Number(fileId || 0)
  if (!id) return false
  return selectedFileIds.value.includes(id)
}

function toggleFileSelection(fileId) {
  const id = Number(fileId || 0)
  if (!id) return
  const idx = selectedFileIds.value.indexOf(id)
  if (idx >= 0) {
    selectedFileIds.value.splice(idx, 1)
    return
  }
  selectedFileIds.value.push(id)
}

function selectAllFiles() {
  selectedFileIds.value = mergedAndSortedFiles.value
    .map(item => Number(item?.file_id || 0))
    .filter(id => id > 0)
}

function clearSelectedFiles() {
  selectedFileIds.value = []
}

function clearPendingDraftFiles() {
  pendingDraftFiles.value = []
}

function clearDraftChatState() {
  clearSelectedFiles()
  clearPendingDraftFiles()
}

function normalizeFileStage(file) {
  const stage = String(file?.processing_stage || '').trim().toLowerCase()
  if (['ready', 'failed', 'indexing', 'parsed', 'parsing', 'uploaded'].includes(stage)) return stage
  const parse = String(file?.parse_status || '').trim().toLowerCase()
  const index = String(file?.index_status || '').trim().toLowerCase()
  if (parse === 'failed' || index === 'failed') return 'failed'
  if (index === 'ready') return 'ready'
  if (index === 'indexing') return 'indexing'
  if (parse === 'parsed') return 'parsed'
  if (parse === 'parsing') return 'parsing'
  return 'uploaded'
}

function fileStatusLabel(file) {
  const stage = normalizeFileStage(file)
  const map = {
    uploaded: '已上传',
    parsing: '解析中',
    parsed: '已解析',
    indexing: '索引中',
    ready: '就绪',
    failed: '失败'
  }
  return map[stage] || '处理中'
}

function fileStatusClass(file) {
  const stage = normalizeFileStage(file)
  return `file-status-${stage}`
}

function hasPendingFileProcessing() {
  return mergedAndSortedFiles.value.some((file) => {
    const stage = normalizeFileStage(file)
    return stage !== 'ready' && stage !== 'failed'
  })
}

function pendingFileProcessingSignature() {
  return mergedAndSortedFiles.value
    .filter((file) => {
      const stage = normalizeFileStage(file)
      return stage !== 'ready' && stage !== 'failed'
    })
    .map((file) => `${Number(file?.file_id || 0)}:${normalizeFileStage(file)}`)
    .sort()
    .join('|')
}

function resetFileStatusPollState() {
  filePollBackoffMs = FILE_POLL_BASE_MS
  filePollConsecutiveFailures = 0
  filePollConsecutiveUnchanged = 0
  filePollLastPendingSignature = ''
  filePollInFlight = false
}

async function refreshFileProcessingStatus() {
  if (filePollInFlight) return { shouldContinue: true }
  filePollInFlight = true
  let refreshed = null
  try {
    refreshed = await store.refreshCurrentChatFiles()
  } catch (error) {
    console.error('[file-status-poll] 刷新失败:', error)
  } finally {
    filePollInFlight = false
  }

  if (!refreshed) {
    filePollConsecutiveFailures += 1
    filePollBackoffMs = Math.min(FILE_POLL_MAX_MS, filePollBackoffMs * 2)
    if (filePollConsecutiveFailures >= FILE_POLL_MAX_FAILURES) {
      console.warn('[file-status-poll] 连续刷新失败，已停止轮询')
      return { shouldContinue: false }
    }
    return { shouldContinue: true }
  }

  filePollConsecutiveFailures = 0
  if (!hasPendingFileProcessing()) return { shouldContinue: false }

  const currentSignature = pendingFileProcessingSignature()
  if (currentSignature && currentSignature === filePollLastPendingSignature) {
    filePollConsecutiveUnchanged += 1
    if (filePollConsecutiveUnchanged >= 3) {
      filePollBackoffMs = Math.min(FILE_POLL_MAX_MS, filePollBackoffMs * 2)
    }
  } else {
    filePollConsecutiveUnchanged = 0
    filePollBackoffMs = FILE_POLL_BASE_MS
    filePollLastPendingSignature = currentSignature
  }

  return { shouldContinue: true }
}

function scheduleFileStatusPolling(delayMs = filePollBackoffMs) {
  if (fileStatusPollTimer) return
  fileStatusPollTimer = setTimeout(async () => {
    fileStatusPollTimer = null
    const result = await refreshFileProcessingStatus()
    if (!result?.shouldContinue) {
      stopFileStatusPolling()
      return
    }
    if (!hasPendingFileProcessing()) {
      stopFileStatusPolling()
      return
    }
    scheduleFileStatusPolling(filePollBackoffMs)
  }, delayMs)
}

function startFileStatusPolling() {
  if (fileStatusPollTimer) return
  if (!hasPendingFileProcessing()) return
  resetFileStatusPollState()
  filePollLastPendingSignature = pendingFileProcessingSignature()
  scheduleFileStatusPolling(FILE_POLL_BASE_MS)
}

function stopFileStatusPolling() {
  if (fileStatusPollTimer) {
    clearTimeout(fileStatusPollTimer)
    fileStatusPollTimer = null
  }
  resetFileStatusPollState()
}

onMounted(async () => {
  pinnedChatsCollapsed.value = localStorage.getItem(PINNED_CHATS_COLLAPSED_KEY) === '1'
  recentChatsCollapsed.value = localStorage.getItem(RECENT_CHATS_COLLAPSED_KEY) === '1'
  fileListCollapsed.value = localStorage.getItem(FILE_LIST_COLLAPSED_KEY) === '1'

  // 从登录状态获取用户信息
  const userStr = localStorage.getItem('user') || localStorage.getItem('agentcode.auth.user.v1')
  if (!userStr) {
    // 如果没有用户信息，跳转到登录页
    window.location.href = '/login'
    return
  }
  
  let currentUser = null
  try {
    const user = JSON.parse(userStr)
    if (!user.id) {
      window.location.href = '/login'
      return
    }
    currentUser = user
    
    // 设置用户ID
    store.setUserId(user.id)
  } catch (e) {
    console.error('解析用户信息失败:', e)
    window.location.href = '/login'
    return
  }
  
  await store.loadChats()
  const isAdmin = currentUser?.role === 'admin' || Number(currentUser?.user_type || 0) === 1
  if (isAdmin) {
    await fetchKbInfo()
  } else {
    store.setKbInfo({ loading: false, size: 0, vectorSize: 0, graphSize: 0, graphConnected: false })
  }
  
  if (store.chats.length > 0) {
    await store.switchChat(store.currentChatId || store.chats[0].id)
    await focusLastQuestionInView({ behavior: 'auto' })
  }

  if (hasPendingFileProcessing()) {
    startFileStatusPolling()
  }
  clampPanelWidths()
  window.addEventListener('resize', clampPanelWidths)
})

onUnmounted(() => {
  store.flushTaskRecoveryPersist()
  recoverableTaskController.detachAllRecoverableTasks()
  stopFileStatusPolling()
  stopPanelResize()
  resetQuestionOutlineState()
  if (scrollFrame !== null) {
    window.cancelAnimationFrame(scrollFrame)
    scrollFrame = null
  }
  window.removeEventListener('resize', clampPanelWidths)
})

async function fetchKbInfo() {
  try {
    const data = await api.getKbInfo()
    const vectorSize = Number(data?.chromadb_size || data?.source_stats?.chromadb || 0)
    const graphSize = Number(data?.source_stats?.neo4j || data?.kb_size || 0)
    const graphConnected = Boolean(data?.source_stats?.neo4j_connected)
    store.setKbInfo({
      loading: false,
      size: vectorSize,
      vectorSize,
      graphSize,
      graphConnected,
    })
  } catch (e) {
    store.setKbInfo({ loading: false, size: 0, vectorSize: 0, graphSize: 0, graphConnected: false })
  }
}

function createNewChat() {
  stopFileStatusPolling()
  clearDraftChatState()
  resetQuestionOutlineState()
  enterDraftChatState()
  inputMessage.value = ''
}

function enterDraftChatState() {
  store.currentChatId = null
  store.persistLocalState()
}

function ensureChatForSend() {
  const currentChatId = normalizeChatId(store.currentChatId)
  if (currentChatId && getChatById(currentChatId)) return currentChatId
  const chat = store.createChat()
  return normalizeChatId(chat?.id)
}

function buildRequestContextForChat(chatId) {
  return buildChatRequestContext({
    chat: getChatById(chatId),
    sessionState: store.sessionState,
    selectedFileIds: selectedFileIds.value,
  })
}

async function discardEmptyChatAfterFailedDraftSend(chatId) {
  const targetChatId = normalizeChatId(chatId)
  const chat = getChatById(targetChatId)
  if (!chat) return
  const hasMessages = Array.isArray(chat.messages) && chat.messages.length > 0
  if (hasMessages) return
  try {
    await store.deleteChat(targetChatId)
  } catch (error) {
    console.warn('[draft-send] 清理空会话失败:', error)
  }
}

function normalizeUploadedPdfForStore(result, file) {
  return {
    file_id: result.document.file_id,
    file_no: Number(result.document.file_no || 0),
    display_no: Number(result.document.display_no || 0),
    pdf_title: result.document.title,
    file_name: file.name,
    pdf_path: result.document.file_path,
    file_hash: result.document.hash,
    uploaded_at: new Date().toISOString(),
    parse_status: result.document.parse_status || 'uploaded',
    index_status: result.document.index_status || 'pending',
    processing_stage: result.document.processing_stage || 'uploaded',
    status_updated_at: new Date().toISOString(),
    last_error: '',
    file_meta: {}
  }
}

function normalizeUploadedExcelForStore(result, file) {
  return {
    file_id: result.document.file_id,
    file_no: Number(result.document.file_no || 0),
    display_no: Number(result.document.display_no || 0),
    excel_title: result.document.title,
    file_name: file.name,
    excel_path: result.document.file_path,
    file_hash: result.document.hash,
    parse_status: result.document.parse_status || 'uploaded',
    index_status: result.document.index_status || 'pending',
    processing_stage: result.document.processing_stage || 'uploaded',
    status_updated_at: new Date().toISOString(),
    last_error: '',
    file_meta: {}
  }
}

async function uploadSingleFileToChat({ file, fileType, chatId, onProgress }) {
  const conversationId = parseInt(chatId, 10)
  if (!conversationId) {
    throw new Error('无法获取对话ID，请刷新页面重试')
  }

  if (fileType === 'pdf') {
    const result = await api.uploadPdf(file, conversationId, onProgress)
    if (!result?.success) {
      throw new Error(result?.error || '上传失败')
    }
    store.addUploadedPdfToChat(chatId, normalizeUploadedPdfForStore(result, file))
    return {
      fileId: Number(result?.document?.file_id || 0),
      title: String(result?.document?.title || file.name || ''),
      document: result.document,
    }
  }

  const result = await api.uploadExcel(file, conversationId)
  if (!result?.success || !result?.document) {
    throw new Error(result?.error || '上传失败')
  }
  store.addUploadedExcelToChat(chatId, normalizeUploadedExcelForStore(result, file))
  return {
    fileId: Number(result?.document?.file_id || 0),
    title: String(result?.document?.title || file.name || ''),
    document: result.document,
  }
}

async function uploadPendingDraftFilesForSend(chatId, titleHint) {
  const targetChatId = normalizeChatId(chatId)
  if (!targetChatId || pendingDraftFiles.value.length === 0) {
    return {
      chatHistory: null,
      conversationId: getChatById(targetChatId)?.synced ? parseInt(targetChatId, 10) : null,
      requestChatContext: buildRequestContextForChat(targetChatId),
    }
  }

  const uploadedIds = []
  const drafts = [...pendingDraftFiles.value]
  uploading.value = true
  uploadProgress.value = 0
  try {
    for (const draft of drafts) {
      const uploaded = await uploadSingleFileToChat({
        file: draft.file,
        fileType: draft.type,
        chatId: targetChatId,
        onProgress: (progress) => {
          uploadProgress.value = progress
        },
      })
      if (uploaded.fileId > 0) {
        uploadedIds.push(uploaded.fileId)
      }
    }
    if (uploadedIds.length > 0) {
      selectedFileIds.value = uploadedIds.reduce(
        (ids, fileId) => mergeSelectedFileIdsAfterUpload(ids, fileId),
        selectedFileIds.value,
      )
    }
    pendingDraftFiles.value = []
    if (hasPendingFileProcessing()) {
      startFileStatusPolling()
    }
    const latestChat = getChatById(targetChatId)
    const chatHistory = (latestChat?.messages || [])
      .filter(m => String(m?.content || '').trim().length > 0)
      .slice(-10)
      .map(m => ({ role: m.role, content: m.content }))
    return {
      chatHistory,
      conversationId: latestChat?.synced ? parseInt(latestChat.id, 10) : null,
      requestChatContext: buildRequestContextForChat(targetChatId),
    }
  } catch (error) {
    pendingDraftFiles.value = drafts
    throw error
  } finally {
    uploading.value = false
    uploadProgress.value = 0
  }
}

async function switchChat(chatId) {
  const nextChatId = normalizeChatId(chatId)
  if (!nextChatId || nextChatId === normalizeChatId(store.currentChatId)) return

  const requestSeq = ++switchChatRequestSeq
  stopFileStatusPolling()
  clearDraftChatState()
  resetQuestionOutlineState()
  await store.switchChat(nextChatId)
  if (requestSeq !== switchChatRequestSeq) return
  await focusLastQuestionInView({ behavior: 'auto' })
  if (hasPendingFileProcessing()) {
    startFileStatusPolling()
  }
}

function handleHistoryItemClick(chatId) {
  if (isHistoryItemDisabled(chatId)) return
  void switchChat(chatId)
}

function deleteChat(chatId) {
  if (isChatBusy(chatId)) return
  if (confirm('确定要删除这个对话吗？')) {
    store.deleteChat(chatId)
  }
}

function toggleChatPinned(chatId) {
  if (isChatBusy(chatId)) return
  store.togglePinned(chatId)
}

function togglePinnedChatsSection() {
  pinnedChatsCollapsed.value = !pinnedChatsCollapsed.value
  localStorage.setItem(PINNED_CHATS_COLLAPSED_KEY, pinnedChatsCollapsed.value ? '1' : '0')
}

function toggleRecentChatsSection() {
  recentChatsCollapsed.value = !recentChatsCollapsed.value
  localStorage.setItem(RECENT_CHATS_COLLAPSED_KEY, recentChatsCollapsed.value ? '1' : '0')
}

function toggleFileListSection() {
  fileListCollapsed.value = !fileListCollapsed.value
  localStorage.setItem(FILE_LIST_COLLAPSED_KEY, fileListCollapsed.value ? '1' : '0')
}

async function attachRecoverableTask({ chatId, taskSummary, replaceMessagesFromServer = false }) {
  return recoverableTaskController.attachRecoverableTask({
    chatId,
    taskSummary,
    replaceMessagesFromServer,
  })
}

async function cancelRecoverableTask(chatId, taskId) {
  return recoverableTaskController.cancelRecoverableTask(chatId, taskId)
}

async function sendTaskMessage(requestedChatId) {
  requestedChatId = normalizeChatId(requestedChatId)
  if (!requestedChatId) return

  const message = inputMessage.value.trim()
  if (!message) return

  const requestChatContext = buildRequestContextForChat(requestedChatId)
  const requestAskMode = selectedAskMode.value
  const titleHint = store.buildAutoTitleFromText(message)
  const result = await recoverableTaskController.sendTaskMessage({
    requestedChatId,
    message,
    titleHint,
    requestChatContext,
    requestAskMode,
    prepareConversation: async ({ chatId }) => uploadPendingDraftFilesForSend(chatId, titleHint),
  })
  if (!result?.ok) {
    if (result.reason === 'capacity_reached') {
      alert('最多同时生成 5 个对话，请先停止一个正在生成的会话。')
      return
    }
    if (result.reason === 'task_create_failed') {
      console.error('[task-create] 创建任务失败:', result.error)
      alert(String(result?.error?.payload?.message || result?.error?.message || '创建任务失败'))
    }
  }
  return result
}

async function sendLegacyMessage(requestedChatId) {
  requestedChatId = normalizeChatId(requestedChatId)
  if (!requestedChatId) return

  const message = inputMessage.value.trim()
  if (!message) return

  const busyStart = store.startChatBusyRuntime(requestedChatId, { phase: 'dispatching' })
  if (!busyStart.ok) {
    if (busyStart.reason === 'capacity_reached') {
      alert('最多同时生成 5 个对话，请先停止一个正在生成的会话。')
    }
    return
  }

  let streamChatId = requestedChatId
  let requestChatContext = buildRequestContextForChat(requestedChatId)
  const requestAskMode = selectedAskMode.value

  try {
    const messageChat = await store.addUserMessage(message, { chatId: requestedChatId })
    streamChatId = normalizeChatId(messageChat?.id || requestedChatId)
    const prepared = await uploadPendingDraftFilesForSend(streamChatId, store.buildAutoTitleFromText(message))
    requestChatContext = prepared.requestChatContext || buildRequestContextForChat(streamChatId)
    if (!store.isChatBusy(streamChatId) || store.isChatStopRequested(streamChatId)) return
    inputMessage.value = ''
    if (normalizeChatId(store.currentChatId) === streamChatId) {
      scrollToBottom({ force: true })
    }

    const streamRequestId = `stream_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
    const runtime = createStreamRuntime(streamChatId, streamRequestId, -1)
    if (runtime?.abortController?.signal.aborted || !store.isChatBusy(streamChatId) || store.isChatStopRequested(streamChatId)) {
      return
    }

    store.addBotMessage({
      role: 'assistant',
      content: '',
      queryMode: '',
      expert: '',
      references: [],
      referenceLinks: [],
      steps: [],
      stepsCollapsed: false,
      isComplete: false,
      streamRequestId
    }, { chatId: streamChatId })

    const targetChatForAssistant = getChatById(streamChatId)
    const targetIndex = Array.isArray(targetChatForAssistant?.messages) ? targetChatForAssistant.messages.length - 1 : -1
    if (runtime) {
      runtime.targetIndex = targetIndex
    }
    store.markChatBusyStreaming(streamChatId, { requestId: streamRequestId, targetMessageIndex: targetIndex })
    if (runtime?.abortController?.signal.aborted || !store.isChatBusy(streamChatId) || store.isChatStopRequested(streamChatId)) {
      updateStreamingTargetMessage(streamChatId, {
        terminalStatus: 'canceled',
        status: 'canceled',
        failureMessage: '用户已停止生成',
        failureCode: 'ASK_CANCELLED',
        retriable: false,
        doneSeen: false,
        metadata: {
          terminal_status: 'canceled',
          status: 'canceled',
          failure_message: '用户已停止生成',
          failure_code: 'ASK_CANCELLED',
          retriable: false,
          done_seen: false,
          streaming_terminal_event: 'canceled',
        },
        isComplete: true
      })
      return
    }

    if (normalizeChatId(store.currentChatId) === streamChatId) {
      scrollToBottom({ force: true })
    }

    const targetChatForRequest = getChatById(streamChatId)
    const chatHistory = (targetChatForRequest?.messages || [])
      .filter(m => String(m?.content || '').trim().length > 0)
      .slice(-10)
      .map(m => ({ role: m.role, content: m.content }))

    const conversationId = targetChatForRequest?.synced ? parseInt(targetChatForRequest.id) : null
    const pdfContext = requestChatContext

    for await (const eventFrame of api.askStream(
      message,
      chatHistory,
      conversationId,
      pdfContext,
      runtime?.abortController?.signal,
      requestAskMode
    )) {
      applyGatewayEvent(streamChatId, eventFrame, runtime)
    }
  } catch (e) {
    flushPendingStreamContent(streamChatId)
    if (e?.name === 'AbortError') {
      return
    }
    const targetMessage = getStreamingTargetMessage(streamChatId)?.message || {}
    if (shouldIgnoreLateStreamError(targetMessage)) {
      return
    }
    const existingMeta = (targetMessage.metadata && typeof targetMessage.metadata === 'object') ? targetMessage.metadata : {}
    const payload = (e?.payload && typeof e.payload === 'object') ? e.payload : {}
    const mergedMeta = mergeRoutingMetadata(existingMeta, payload)
    const errorMessage = String(payload?.message || e?.message || '未知错误')
    const presentation = buildRoutingErrorPresentation({
      code: payload?.code,
      message: errorMessage,
      metadata: mergedMeta,
      data: payload?.data,
    })
    if (presentation.kind === 'quota_card' && presentation.card) {
      mergedMeta.quota_card = presentation.card
    } else {
      delete mergedMeta.quota_card
    }
    const renderedError = presentation.kind === 'markdown'
      ? presentation.markdown
      : errorMessage
    updateStreamingTargetMessage(streamChatId, {
      content: renderedError,
      queryMode: getActualQueryModeLabel(payload, mergedMeta, { allowRouteFallback: false }) || targetMessage.queryMode || '',
      metadata: {
        ...mergedMeta,
        streaming_terminal_event: 'error',
      },
      isComplete: true
    })
  } finally {
    resetStreamFlushState(streamChatId)
    clearStreamRuntime(streamChatId)
    store.finishChatBusyRuntime(streamChatId)
    if (normalizeChatId(store.currentChatId) === streamChatId) {
      scrollToBottom()
    }
  }
}

async function sendMessage() {
  if (!canSend.value) {
    if (isCurrentChatBusy.value) stopStreaming(store.currentChatId)
    return
  }

  const hadActiveChatBeforeSend = Boolean(getChatById(normalizeChatId(store.currentChatId)))
  const requestedChatId = ensureChatForSend()
  if (!requestedChatId) return
  const createdChatForSend = !hadActiveChatBeforeSend

  if (store.refreshSurvivableQATasksEnabled) {
    const result = await sendTaskMessage(requestedChatId)
    if (!result?.ok && createdChatForSend) {
      await discardEmptyChatAfterFailedDraftSend(result?.chatId)
      await discardEmptyChatAfterFailedDraftSend(requestedChatId)
      await discardEmptyChatAfterFailedDraftSend(store.currentChatId)
    }
    return result
  }
  try {
    return await sendLegacyMessage(requestedChatId)
  } catch (error) {
    if (createdChatForSend) {
      await discardEmptyChatAfterFailedDraftSend(requestedChatId)
    }
    throw error
  }
}

function stopStreaming(chatId = store.currentChatId) {
  const targetChatId = normalizeChatId(chatId)
  if (!targetChatId) return
  const chat = getChatById(targetChatId)
  const activeTaskId = String(chat?.activeTask?.task_id || '').trim()
  store.requestChatBusyStop(targetChatId)
  if (store.refreshSurvivableQATasksEnabled && activeTaskId) {
    void cancelRecoverableTask(targetChatId, activeTaskId)
    return
  }
  const runtime = getStreamRuntime(targetChatId)
  runtime?.abortController?.abort()
  flushPendingStreamContent(targetChatId)
  const targetMessage = getStreamingTargetMessage(targetChatId)?.message
  if (!targetMessage) {
    return
  }
  const existingMeta = (targetMessage?.metadata && typeof targetMessage.metadata === 'object') ? targetMessage.metadata : {}
  updateStreamingTargetMessage(targetChatId, {
    terminalStatus: 'canceled',
    status: 'canceled',
    failureMessage: '用户已停止生成',
    failureCode: 'ASK_CANCELLED',
    retriable: false,
    doneSeen: false,
    metadata: {
      ...existingMeta,
      terminal_status: 'canceled',
      status: 'canceled',
      failure_message: '用户已停止生成',
      failure_code: 'ASK_CANCELLED',
      retriable: false,
      done_seen: false,
      streaming_terminal_event: 'canceled',
    },
    isComplete: true
  })
  if (normalizeChatId(store.currentChatId) === targetChatId) {
    scrollToBottom()
  }
}

function revealHiddenHistory(targetAbsoluteIndex = null) {
  const totalMessages = store.currentMessages.length
  const shouldWindow = totalMessages > MESSAGE_WINDOW_THRESHOLD
  if (!shouldWindow) return false

  const visibleCount = DEFAULT_VISIBLE_MESSAGE_COUNT
  if (targetAbsoluteIndex === null || targetAbsoluteIndex === undefined) {
    const nextRevealCount = Math.min(
      revealedHiddenMessageCount.value + HISTORY_REVEAL_BATCH_SIZE,
      Math.max(0, totalMessages - visibleCount)
    )
    const changed = nextRevealCount !== revealedHiddenMessageCount.value
    revealedHiddenMessageCount.value = nextRevealCount
    return changed
  }

  const decision = resolveHiddenHistoryReveal({
    totalMessages,
    visibleCount,
    revealedCount: revealedHiddenMessageCount.value,
    batchSize: HISTORY_REVEAL_BATCH_SIZE,
    targetAbsoluteIndex,
  })
  revealedHiddenMessageCount.value = decision.nextRevealedCount
  return decision.needsReveal
}

function updateQuestionOutlineItems() {
  questionOutlineItems.value = buildQuestionOutlineItems(store.currentMessages)
}

function updateNearBottomState() {
  if (!messagesArea.value) {
    isNearBottomRef.value = true
    pendingAutoScroll.value = false
    return
  }

  const nearBottom = isNearBottom({
    scrollTop: messagesArea.value.scrollTop,
    clientHeight: messagesArea.value.clientHeight,
    scrollHeight: messagesArea.value.scrollHeight,
    thresholdPx: DEFAULT_NEAR_BOTTOM_THRESHOLD_PX,
  })
  isNearBottomRef.value = nearBottom
  if (nearBottom) {
    pendingAutoScroll.value = false
  }
}

function handleMessagesScroll() {
  updateNearBottomState()
}

function scrollToBottom(options = {}) {
  const force = Boolean(options?.force)
  if (!shouldAutoScroll({ force, nearBottom: isNearBottomRef.value })) {
    pendingAutoScroll.value = true
    return
  }

  pendingAutoScroll.value = false
  if (scrollFrame !== null) return
  scrollFrame = window.requestAnimationFrame(() => {
    scrollFrame = null
    nextTick(() => {
      if (messagesArea.value) {
        messagesArea.value.scrollTop = messagesArea.value.scrollHeight
        updateNearBottomState()
      }
    })
  })
}

function autoResize(e) {
  e.target.style.height = 'auto'
  e.target.style.height = e.target.scrollHeight + 'px'
}

function triggerFileUpload() {
  fileInput.value?.click()
}

function resetFileInput() {
  if (fileInput.value) {
    fileInput.value.value = ''
  }
}

function queuePendingDraftFile(file, fileType) {
  const draft = buildPendingDraftFile(file, { type: fileType })
  pendingDraftFiles.value = [...pendingDraftFiles.value, draft]
  const defaultPrompt = defaultPromptForDraftFileType(fileType)
  if (!inputMessage.value.trim() && defaultPrompt) {
    inputMessage.value = defaultPrompt
  }
  resetFileInput()
}

async function handleFileSelect(event) {
  const file = event.target.files?.[0]
  if (!file) return
  
  const fileType = resolveDraftFileType(file.name)
  if (!fileType) {
    alert('只支持PDF、Excel和CSV文件')
    resetFileInput()
    return
  }

  if (!store.currentChat) {
    queuePendingDraftFile(file, fileType)
    return
  }

  if (!store.currentChat.synced) {
    alert('请先发送问题创建会话后再上传文件')
    resetFileInput()
    return
  }

  uploading.value = true
  uploadProgress.value = 0
  const uploadConversationTitle = store.buildAutoTitleFromFileName(file.name)
  
  try {
    const uploaded = await uploadSingleFileToChat({
      file,
      fileType,
      chatId: store.currentChat.id,
      onProgress: (progress) => {
        uploadProgress.value = progress
      }
    })
    if (uploaded.fileId > 0) {
      selectedFileIds.value = mergeSelectedFileIdsAfterUpload(selectedFileIds.value, uploaded.fileId)
    }
    const parsedTitle = String(uploaded.title || '').trim()
    if (parsedTitle && store.currentChat?.title === uploadConversationTitle) {
      await store.updateCurrentChatTitle(parsedTitle, { persist: true })
    }
    if (store.currentChat?.title === '新对话') {
      await store.updateCurrentChatTitle(uploadConversationTitle, { persist: true, onlyIfPlaceholder: true })
    }
    store.addSystemMessage(`✅ ${fileType === 'pdf' ? 'PDF' : 'Excel'}上传成功: ${uploaded.title || file.name} (#${resolveUploadedFileDisplayNumber(uploaded.document)})`)
    const defaultPrompt = defaultPromptForDraftFileType(fileType)
    if (defaultPrompt) inputMessage.value = defaultPrompt
    startFileStatusPolling()
  } catch (error) {
    alert('上传失败: ' + error.message)
  } finally {
    uploading.value = false
    uploadProgress.value = 0
    resetFileInput()
  }
}

async function handleRemovePdf(pdfId) {
  if (confirm('确定要删除这个PDF吗？删除后将不再使用此PDF回答问题。')) {
    selectedFileIds.value = selectedFileIds.value.filter(id => Number(id) !== Number(pdfId))
    try {
      await store.removePdf(pdfId)
    } catch (error) {
      alert(`删除PDF失败: ${error?.message || '未知错误'}`)
    }
  }
}

function downloadUploadedFile(file) {
  const conversationId = parseInt(store.currentChat?.id || '0', 10)
  if (!file?.file_id || !Number.isFinite(conversationId) || conversationId <= 0) {
    return
  }
  const token = localStorage.getItem('token') || ''
  let url = `/api/conversations/${conversationId}/files/${file.file_id}/download`
  if (token) {
    url += `?token=${encodeURIComponent(token)}`
  }
  window.open(url, '_blank')
}

async function handleRemoveExcel(excelId) {
  if (confirm('确定要删除这个Excel文件吗？')) {
    selectedFileIds.value = selectedFileIds.value.filter(id => Number(id) !== Number(excelId))
    try {
      await store.removeExcel(excelId)
    } catch (error) {
      alert(`删除文件失败: ${error?.message || '未知错误'}`)
    }
  }
}

watch(mergedAndSortedFiles, () => {
  normalizeSelectedFileIds()
}, { deep: true })

watch(
  () => normalizeChatId(store.currentChatId),
  () => {
    resetHiddenHistoryState()
  },
  { immediate: true }
)

watch(() => [
  currentRecoverableTaskSnapshot.value.chatId,
  currentRecoverableTaskSnapshot.value.taskId,
  currentRecoverableTaskSnapshot.value.status,
  currentRecoverableTaskSnapshot.value.replayAvailable ? '1' : '0',
], ([chatId, taskId, status, replayAvailable]) => {
  if (!store.refreshSurvivableQATasksEnabled) return
  if (!chatId) return
  if (!String(taskId || '').trim()) return
  const taskSummary = getChatById(chatId)?.activeTask
  const cursor = normalizeTaskReplayCursor({
    task_id: taskId,
    status,
    last_seq: store.getChatLastTaskSeq(chatId),
    replay_available: replayAvailable === '1',
  }, store.getChatLastTaskSeq(chatId))
  if (!cursor.recoverable) return
  const existingRuntime = getStreamRuntime(chatId)
  if (existingRuntime?.mode === 'task' && existingRuntime?.requestId === cursor.taskId && !existingRuntime?.abortController?.signal?.aborted) {
    taskRecoveryDebug.log('home:attach-watch-skip-active-runtime', {
      chatId,
      taskId: cursor.taskId,
      status: cursor.status,
      localLastSeq: store.getChatLastTaskSeq(chatId),
    })
    return
  }
  void attachRecoverableTask({
    chatId,
    taskSummary,
  })
}, { immediate: true })

watch(
  () => [normalizeChatId(store.currentChatId), questionOutlineSignature.value],
  () => {
    updateQuestionOutlineItems()
    focusLastQuestionInView({ scroll: false })
    nextTick(() => {
      updateNearBottomState()
    })
  },
  { immediate: true }
)
</script>

<template>
  <div ref="appContainer" class="app-container" :class="{ resizing: isPanelResizing }">
    <aside
      class="sidebar"
      :class="{ collapsed: leftSidebarCollapsed }"
      :style="{ width: `${currentLeftSidebarWidth}px` }"
    >
      <div class="sidebar-header" :class="{ collapsed: leftSidebarCollapsed }">
        <div v-if="!leftSidebarCollapsed" class="sidebar-title-group">
          <div class="sidebar-title">对话历史</div>
          <button class="new-chat-btn" type="button" @click="createNewChat">新建对话</button>
        </div>
        <button class="sidebar-toggle-btn" type="button" @click="toggleLeftSidebar">
          {{ leftSidebarCollapsed ? '展开' : '收起' }}
        </button>
      </div>

      <template v-if="!leftSidebarCollapsed">
        <div class="system-info-section">
          <div class="info-title">💡 系统说明</div>
          <div class="info-content">
            <p>• 基于预加载的磷酸铁锂相关文献</p>
            <p>• 支持知识图谱、文献检索、社区分析</p>
          </div>
        </div>
        <div class="chat-history">
          <div v-if="store.chats.length === 0" class="empty-history">暂无对话</div>
          <template v-else>
            <section v-if="pinnedChats.length > 0" class="history-group">
              <button class="history-group-header" type="button" @click="togglePinnedChatsSection">
                <span class="history-group-title-wrap">
                  <span class="history-group-toggle">{{ pinnedChatsCollapsed ? '▶' : '▼' }}</span>
                  <span class="history-group-title">已置顶</span>
                </span>
                <span class="history-group-count">{{ pinnedChats.length }}</span>
              </button>
              <div v-show="!pinnedChatsCollapsed" class="history-group-list">
                <div 
                  v-for="chat in pinnedChats" 
                  :key="chat.id"
                  class="history-item"
                  :class="{
                    active: chat.id === store.currentChatId,
                    pinned: chat.isPinned,
                    disabled: isHistoryItemDisabled(chat.id),
                    streaming: isStreamingChat(chat.id)
                  }"
                  :aria-disabled="isHistoryItemDisabled(chat.id)"
                  :title="getHistoryItemTitle(chat.id)"
                  @click="handleHistoryItemClick(chat.id)"
                >
                  <div class="history-title">
                    <span class="history-title-text">{{ chat.title }}</span>
                    <div class="history-title-actions">
                      <span v-if="getTaskPhaseLabel(chat.id)" class="history-status-badge">{{ getTaskPhaseLabel(chat.id) }}</span>
                      <button
                        v-if="isChatBusy(chat.id)"
                        class="history-stop-btn"
                        type="button"
                        title="停止生成"
                        @click.stop="stopStreaming(chat.id)"
                      >
                        停止
                      </button>
                      <button
                        class="pin-chat-btn"
                        :class="{ pinned: chat.isPinned }"
                        type="button"
                        :disabled="isChatBusy(chat.id)"
                        :title="isChatBusy(chat.id) ? '生成中不可置顶/取消置顶' : (chat.isPinned ? '取消置顶' : '置顶对话')"
                        @click.stop="toggleChatPinned(chat.id)"
                      >
                        {{ chat.isPinned ? '★' : '☆' }}
                      </button>
                      <button
                        class="history-delete-btn"
                        type="button"
                        :disabled="isChatBusy(chat.id)"
                        :title="isChatBusy(chat.id) ? '生成中不可删除' : '删除对话'"
                        @click.stop="deleteChat(chat.id)"
                      >
                        删除
                      </button>
                      <span v-if="getChatSyncStatus(chat) === 'failed'" class="sync-icon sync-failed" title="同步失败">⚠️</span>
                      <span v-else-if="getChatSyncStatus(chat) === 'syncing'" class="sync-icon sync-syncing" title="同步中">🔄</span>
                      <span v-else-if="getChatSyncStatus(chat) === 'synced'" class="sync-icon sync-synced" title="已同步">☁️</span>
                    </div>
                  </div>
                  <div class="history-time">{{ formatTime(chat.updatedAt || chat.createdAt) }}</div>
                </div>
              </div>
            </section>

            <section class="history-group">
              <button class="history-group-header" type="button" @click="toggleRecentChatsSection">
                <span class="history-group-title-wrap">
                  <span class="history-group-toggle">{{ recentChatsCollapsed ? '▶' : '▼' }}</span>
                  <span class="history-group-title">最近会话</span>
                </span>
                <span class="history-group-count">{{ recentChats.length }}</span>
              </button>
              <div v-if="recentChats.length === 0 && !recentChatsCollapsed" class="empty-history-group">暂无最近会话</div>
              <div v-show="!recentChatsCollapsed" class="history-group-list">
                <div 
                  v-for="chat in recentChats" 
                  :key="chat.id"
                  class="history-item"
                  :class="{
                    active: chat.id === store.currentChatId,
                    pinned: chat.isPinned,
                    disabled: isHistoryItemDisabled(chat.id),
                    streaming: isStreamingChat(chat.id)
                  }"
                  :aria-disabled="isHistoryItemDisabled(chat.id)"
                  :title="getHistoryItemTitle(chat.id)"
                  @click="handleHistoryItemClick(chat.id)"
                >
                  <div class="history-title">
                    <span class="history-title-text">{{ chat.title }}</span>
                    <div class="history-title-actions">
                      <span v-if="getTaskPhaseLabel(chat.id)" class="history-status-badge">{{ getTaskPhaseLabel(chat.id) }}</span>
                      <button
                        v-if="isChatBusy(chat.id)"
                        class="history-stop-btn"
                        type="button"
                        title="停止生成"
                        @click.stop="stopStreaming(chat.id)"
                      >
                        停止
                      </button>
                      <button
                        class="pin-chat-btn"
                        :class="{ pinned: chat.isPinned }"
                        type="button"
                        :disabled="isChatBusy(chat.id)"
                        :title="isChatBusy(chat.id) ? '生成中不可置顶/取消置顶' : (chat.isPinned ? '取消置顶' : '置顶对话')"
                        @click.stop="toggleChatPinned(chat.id)"
                      >
                        {{ chat.isPinned ? '★' : '☆' }}
                      </button>
                      <button
                        class="history-delete-btn"
                        type="button"
                        :disabled="isChatBusy(chat.id)"
                        :title="isChatBusy(chat.id) ? '生成中不可删除' : '删除对话'"
                        @click.stop="deleteChat(chat.id)"
                      >
                        删除
                      </button>
                      <span v-if="getChatSyncStatus(chat) === 'failed'" class="sync-icon sync-failed" title="同步失败">⚠️</span>
                      <span v-else-if="getChatSyncStatus(chat) === 'syncing'" class="sync-icon sync-syncing" title="同步中">🔄</span>
                      <span v-else-if="getChatSyncStatus(chat) === 'synced'" class="sync-icon sync-synced" title="已同步">☁️</span>
                    </div>
                  </div>
                  <div class="history-time">{{ formatTime(chat.updatedAt || chat.createdAt) }}</div>
                </div>
              </div>
            </section>
          </template>
        </div>
      </template>

      <div v-else class="sidebar-collapsed-body">
        <button class="collapsed-new-chat-btn" type="button" @click="createNewChat" title="新建对话">＋</button>
        <div class="collapsed-chat-count" :title="`当前共有 ${store.chats.length} 个对话`">
          {{ store.chats.length }}
        </div>
      </div>
    </aside>
    <div
      class="panel-splitter"
      title="拖拽调整左侧栏宽度"
      @mousedown="startPanelResize('left', $event)"
    ></div>

    <main class="main-chat">
      <header class="chat-header">
        <div class="header-left">
          <div class="ai-icon">✨</div>
          <div class="header-title">
            <h1>磷酸铁锂知识图谱 AI</h1>
          </div>
        </div>
        <div class="header-right">
          <a href="/profile" class="nav-link">个人中心</a>
          <a href="/admin" class="nav-link admin-only">管理后台</a>
        </div>
      </header>

      <!-- 文件列表显示 (PDF + Excel统一编号) -->
      <div v-if="hasVisibleConversationFiles" class="pdf-list-section">
        <div class="pdf-list-header">
          <button class="pdf-list-toggle-btn" type="button" @click="toggleFileListSection">
            <span class="history-group-title-wrap">
              <span class="history-group-toggle">{{ fileListCollapsed ? '▶' : '▼' }}</span>
              <span class="pdf-list-title">📎 对话文件</span>
            </span>
            <span class="history-group-count">{{ visibleConversationFileCount }}</span>
          </button>
          <div class="pdf-list-actions">
            <button class="pdf-action-btn" @click="selectAllFiles" :disabled="!hasSelectableConversationFiles">全选</button>
            <button class="pdf-action-btn" @click="clearSelectedFiles" :disabled="!hasSelectableConversationFiles && selectedFileIds.length === 0">清空</button>
            <span class="pdf-select-tip">已选 {{ selectedFileIds.length }} 个（不选则使用知识库问答）</span>
          </div>
        </div>
        <div v-show="!fileListCollapsed" class="pdf-list-items">
          <!-- 合并并按file_id排序显示 -->
          <template v-for="file in visibleConversationFiles" :key="file.type + '-' + (file.id || file.draftId)">
            <div
              class="pdf-list-item"
              :class="{
                selected: !isDraftFileItem(file) && isFileSelected(file.file_id),
                'pdf-list-item-draft': isDraftFileItem(file)
              }"
              @click="!isDraftFileItem(file) && toggleFileSelection(file.file_id)"
            >
              <label v-if="!isDraftFileItem(file)" class="file-select-wrap" title="选择文件参与本轮问答" @click.stop>
                <input
                  type="checkbox"
                  :checked="isFileSelected(file.file_id)"
                  @change="toggleFileSelection(file.file_id)"
                >
              </label>
              <span v-else class="file-select-spacer" aria-hidden="true"></span>
              <span class="pdf-number">
                <template v-if="isDraftFileItem(file)">{{ file.displayLabel }}</template>
                <template v-else>#{{ file.display_no || file.file_no || file.file_id }}</template>
              </span>
              <span class="pdf-icon">{{ getFileIcon(file) }}</span>
              <span class="pdf-title">{{ file.title }}</span>
              <span
                class="file-status-badge"
                :class="isDraftFileItem(file) ? 'file-status-draft' : fileStatusClass(file)"
                :title="isDraftFileItem(file) ? file.statusLabel : (file.last_error || fileStatusLabel(file))"
              >
                {{ isDraftFileItem(file) ? file.statusLabel : fileStatusLabel(file) }}
              </span>
              <span v-if="!isDraftFileItem(file) && store.sessionState.newlyUploadedPdfIds.includes(file.file_id)" class="pdf-new-badge">新</span>
              <button v-if="!isDraftFileItem(file)" class="file-download-btn" @click.stop="downloadUploadedFile(file)" title="下载文件">下载</button>
              <button
                v-if="isDraftFileItem(file)"
                class="pdf-remove-btn"
                type="button"
                @click.stop="removePendingDraftFile(file.draftId)"
                :disabled="isCurrentChatBusy || uploading"
                :title="(isCurrentChatBusy || uploading) ? '当前不可移除待发送文件' : '移除待发送文件'"
              >×</button>
              <button
                v-else
                class="pdf-remove-btn"
                @click.stop="file.type === 'pdf' ? handleRemovePdf(file.file_id) : handleRemoveExcel(file.file_id)"
                :disabled="isCurrentChatBusy"
                :title="isCurrentChatBusy ? '生成中不可删除文件' : '删除'"
              >×</button>
            </div>
          </template>
        </div>
      </div>

      <div class="messages-area" ref="messagesArea" @scroll="handleMessagesScroll">
        <template v-if="!hasMessages">
          <div class="empty-state">
            <div class="empty-icon">🔋</div>
            <div class="empty-title">你好！我是磷酸铁锂材料专家</div>
            <div>请提出您的问题</div>
          </div>
        </template>
        <template v-else>
          <div v-if="hiddenHistoryCount > 0" class="hidden-history-banner">
            <button class="hidden-history-btn" type="button" @click="revealHiddenHistory()">
              查看更早消息（{{ hiddenHistoryCount }}）
            </button>
          </div>
          <div
            v-for="entry in visibleMessageEntries"
            :key="entry.absoluteMessageIndex"
            v-memo="[getMessageRenderMemoKey(entry.message), highlightedQuestionMessageIndex === entry.absoluteMessageIndex]"
            class="message"
            :data-message-index="entry.absoluteMessageIndex"
            :class="[
              'message-' + entry.message.role,
              {
                'message-question-anchor': entry.message.role === 'user',
                'message-highlighted': highlightedQuestionMessageIndex === entry.absoluteMessageIndex
              }
            ]"
            :id="entry.message.role === 'user' ? getQuestionAnchorId(entry.absoluteMessageIndex) : undefined"
            :ref="entry.message.role === 'user' ? (el) => setUserMessageElement(entry.absoluteMessageIndex, el) : null"
          >
            <template v-if="entry.message.role === 'user'">
              <div class="message-content">{{ entry.message.content }}</div>
            </template>
            <template v-else-if="entry.message.role === 'system'">
              <div class="system-message">
                <span class="system-icon">ℹ️</span>
                <span class="system-text">{{ entry.message.content }}</span>
              </div>
            </template>
            <template v-else-if="entry.message.role === 'bot' || entry.message.role === 'assistant'">
              <div class="bot-avatar">✨</div>
              <div class="message-content" :class="{ 'message-graph-kb': isGraphKbMessage(entry.message), 'message-patent': isPatentMessage(entry.message) }">
                <div v-if="entry.message.queryMode" class="query-mode-badge">{{ entry.message.queryMode }}</div>
                <div v-if="hasProcessPanel(entry.message)" class="steps-panel">
                  <div class="steps-header" @click="toggleSteps(entry.absoluteMessageIndex)">
                    <div class="steps-title">
                      <span class="steps-toggle">{{ isStepsCollapsed(entry.message) ? '▶' : '▼' }}</span>
                      <span>处理过程</span>
                      <span v-if="getStepPanelCount(entry.message) > 0" class="steps-count">{{ getStepPanelCount(entry.message) }}</span>
                    </div>
                    <div class="steps-meta">
                      <span v-if="getStageTimingSummary(entry.message)" class="stage-timing-summary">
                        {{ getStageTimingSummary(entry.message) }}
                      </span>
                      <span v-if="getStepOverview(entry.message)" class="steps-overview">{{ getStepOverview(entry.message) }}</span>
                      <div v-if="isStepsCollapsed(entry.message) && getCollapsedStepSummary(entry.message)" class="steps-summary">
                        <span class="step-icon" :class="'step-icon-' + normalizeStepStatus(getCollapsedStepSummary(entry.message).status)">
                          {{ getStepIcon(getCollapsedStepSummary(entry.message)) }}
                        </span>
                        <span class="step-message">{{ getStepTitle(getCollapsedStepSummary(entry.message)) }}</span>
                        <span v-if="getVisibleStepCount(getCollapsedStepSummary(entry.message))" class="step-badge">
                          {{ getVisibleStepCount(getCollapsedStepSummary(entry.message)) }}
                        </span>
                      </div>
                    </div>
                  </div>
                  <div v-show="!isStepsCollapsed(entry.message)" class="processing-steps">
                    <div v-for="(step, idx) in getVisibleMessageSteps(entry.message)" :key="step.step || idx" class="step-item" :class="'step-' + normalizeStepStatus(step.status)">
                      <span class="step-icon" :class="'step-icon-' + normalizeStepStatus(step.status)">{{ getStepIcon(step) }}</span>
                      <div class="step-body">
                        <div class="step-row">
                          <span class="step-title">{{ getStepTitle(step) }}</span>
                          <span v-if="getVisibleStepCount(step)" class="step-badge">{{ getVisibleStepCount(step) }}</span>
                          <span v-if="!getStepDetail(step) && getStepTimingDurationLabel(entry.message, step)" class="stage-step-duration">
                            {{ getStepTimingDurationLabel(entry.message, step) }}
                          </span>
                        </div>
                        <div v-if="getStepDetail(step)" class="step-detail">
                          <span>{{ getStepDetail(step) }}</span>
                          <span v-if="getStepTimingDurationLabel(entry.message, step)" class="stage-step-duration">
                            {{ getStepTimingDurationLabel(entry.message, step) }}
                          </span>
                        </div>
                        <div v-if="step.error" class="step-error-text">{{ step.error }}</div>
                      </div>
                    </div>
                  </div>
                </div>
                <div v-if="getPatentPreviewStreams(entry.message).length > 0" class="patent-preview-panel">
                  <div class="patent-preview-header">
                    <span class="patent-preview-title">处理中证据</span>
                    <span class="patent-preview-count">{{ getPatentPreviewStreams(entry.message).length }}</span>
                  </div>
                  <div v-if="isPatentFinalAnswerPending(entry.message)" class="patent-preview-pending">正在汇总最终答案...</div>
                  <div
                    v-for="stream in getPatentPreviewStreams(entry.message)"
                    :key="stream.contentStreamId"
                    class="patent-preview-stream"
                  >
                    <div class="patent-preview-stream-header">
                      <span class="patent-preview-stream-title">{{ getPatentPreviewSourceLabel(stream) }}</span>
                      <span class="patent-preview-stream-status">{{ stream.completed ? '已完成' : '生成中' }}</span>
                    </div>
                    <div
                      v-if="stream.content"
                      class="patent-preview-stream-body"
                    >
                      <MarkdownRenderer
                        :content="String(stream.content || '')"
                        variant="patent-preview"
                        @open-doi="(doi) => handleMarkdownDoiOpen(doi, entry.absoluteMessageIndex)"
                        @open-patent="handleMarkdownPatentOpen"
                      />
                    </div>
                    <div v-else class="patent-preview-stream-empty">处理中...</div>
                  </div>
                </div>
                <QuotaLimitCard v-if="getQuotaCard(entry.message)" :card="getQuotaCard(entry.message)" />
                <div
                  v-else-if="entry.message.content && isStreamingTextMessage(entry.message)"
                  class="message-markdown-content"
                  :class="{ 'graph-kb-markdown': isGraphKbMessage(entry.message), 'patent-markdown': isPatentMessage(entry.message) }"
                >
                  <MarkdownRenderer
                    :content="String(entry.message.content || '')"
                    :streaming="true"
                    :variant="getMessageMarkdownVariant(entry.message)"
                    @open-doi="(doi) => handleMarkdownDoiOpen(doi, entry.absoluteMessageIndex)"
                    @open-patent="handleMarkdownPatentOpen"
                  />
                </div>
                <template v-else-if="entry.message.content">
                  <div
                    class="message-markdown-content"
                    :class="{ 'graph-kb-markdown': isGraphKbMessage(entry.message), 'patent-markdown': isPatentMessage(entry.message) }"
                  >
                    <MarkdownRenderer
                      :content="String(entry.message.content || '')"
                      :variant="getMessageMarkdownVariant(entry.message)"
                      @open-doi="(doi) => handleMarkdownDoiOpen(doi, entry.absoluteMessageIndex)"
                      @open-patent="handleMarkdownPatentOpen"
                    />
                  </div>
                  <div v-if="getTerminalMessageState(entry.message)" class="terminal-message-inline" :class="'terminal-message-' + getTerminalMessageState(entry.message)">
                    <div class="terminal-message-title">{{ getTerminalMessageTitle(entry.message) }}</div>
                    <div v-if="getTerminalMessageDetail(entry.message)" class="terminal-message-detail">{{ getTerminalMessageDetail(entry.message) }}</div>
                  </div>
                </template>
                <div v-else-if="getTerminalMessageState(entry.message)" class="terminal-message-card" :class="'terminal-message-' + getTerminalMessageState(entry.message)">
                  <div class="terminal-message-title">{{ getTerminalMessageTitle(entry.message) }}</div>
                  <div v-if="getTerminalMessageDetail(entry.message)" class="terminal-message-detail">{{ getTerminalMessageDetail(entry.message) }}</div>
                </div>
                <div v-else-if="getTaskPhaseLabel(store.currentChatId)" class="loading-animation"><span>{{ getTaskPhaseLabel(store.currentChatId) }}...</span></div>
                <div v-else class="loading-animation"><span>思考中...</span></div>
              </div>
            </template>
          </div>
        </template>
      </div>

      <div class="input-area">
        <!-- 上传进度 -->
        <div v-if="uploading" class="upload-progress">
          <div class="progress-bar">
            <div class="progress-fill" :style="{width: uploadProgress + '%'}"></div>
          </div>
          <span class="progress-text">上传中 {{ uploadProgress }}%</span>
        </div>
        
        <div class="ask-mode-toolbar">
          <span class="ask-mode-label">问答模式</span>
          <div class="ask-mode-group">
            <button
              v-for="option in askModeOptions"
              :key="option.value"
              type="button"
              class="ask-mode-btn"
              :class="{ active: selectedAskMode === option.value }"
              @click="setAskMode(option.value)"
              :disabled="selectedAskMode === option.value || isCurrentChatBusy"
            >
              {{ option.label }}
            </button>
          </div>
        </div>

        <div class="input-wrapper">
          <input 
            type="file" 
            ref="fileInput" 
            accept=".pdf,.xlsx,.xls,.csv" 
            @change="handleFileSelect" 
            style="display: none"
          />
          <button 
            class="upload-btn" 
            @click="triggerFileUpload" 
            :disabled="uploading || isCurrentChatBusy"
            title="上传文件（PDF/Excel/CSV）"
          >
            📎
          </button>
          <textarea v-model="inputMessage" placeholder="问我任何关于磷酸铁锂的问题..." rows="1" @keydown.enter.prevent="sendMessage" @input="autoResize($event)"></textarea>
          <button class="send-btn" :disabled="!canToggleStreaming" @click="sendMessage">{{ isCurrentChatBusy ? '⏹' : '➤' }}</button>
        </div>
      </div>
    </main>
    <div
      class="panel-splitter"
      title="拖拽调整右侧栏宽度"
      @mousedown="startPanelResize('right', $event)"
    ></div>

    <aside
      class="question-outline"
      :class="{ collapsed: questionOutlineCollapsed }"
      :style="{ width: `${currentRightPanelWidth}px` }"
    >
      <div class="question-outline-header">
        <div v-if="!questionOutlineCollapsed" class="question-outline-title-group">
          <div class="question-outline-title">本对话问题</div>
          <div class="question-outline-count">共 {{ questionOutlineItems.length }} 条</div>
        </div>
        <button
          class="question-outline-toggle"
          type="button"
          @click="toggleQuestionOutline"
        >
          {{ questionOutlineCollapsed ? '展开' : '收起' }}
        </button>
      </div>
      <div v-if="!questionOutlineCollapsed" class="question-outline-body">
        <div v-if="questionOutlineItems.length === 0" class="question-outline-empty">
          当前对话还没有提问记录
        </div>
        <button
          v-for="item in questionOutlineItems"
          :key="item.anchorId"
          class="question-outline-item"
          :class="{ active: activeQuestionMessageIndex === item.messageIndex }"
          type="button"
          @click="scrollToQuestion(item)"
        >
          <span class="outline-index">Q{{ item.outlineIndex }}</span>
          <span class="outline-text">{{ item.preview }}</span>
        </button>
      </div>
    </aside>

    <PdfReader ref="pdfReader" />
  </div>
</template>

<style scoped>
.chat-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.header-right {
  margin-left: auto;
  display: flex;
  gap: 12px;
}

.nav-link {
  color: #667eea;
  text-decoration: none;
  font-size: 14px;
  padding: 8px 16px;
  border: 1px solid #667eea;
  border-radius: 6px;
  transition: all 0.2s;
}

.nav-link:hover {
  background: #667eea;
  color: white;
}

.admin-only {
  display: none;
}

.app-container {
  display: flex;
  min-height: 100vh;
  background: #f8fafc;
  overflow: hidden;
}

.app-container.resizing {
  user-select: none;
}

.app-container.resizing * {
  cursor: col-resize !important;
}

.sidebar {
  flex-shrink: 0;
  background: white;
  border-right: 1px solid #e2e8f0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.sidebar.collapsed {
  border-right-color: transparent;
}

.sidebar-header {
  padding: 20px;
  border-bottom: 1px solid #e2e8f0;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.sidebar-header.collapsed {
  padding: 14px 12px;
  align-items: center;
}

.sidebar-title-group {
  min-width: 0;
  flex: 1;
}

.sidebar-title {
  font-weight: 600;
  color: #1e293b;
  margin-bottom: 12px;
}

.sidebar-toggle-btn {
  border: 1px solid #cbd5e1;
  background: #fff;
  color: #334155;
  border-radius: 8px;
  padding: 6px 10px;
  font-size: 12px;
  cursor: pointer;
  white-space: nowrap;
}

.sidebar-toggle-btn:hover {
  border-color: #94a3b8;
  background: #f8fafc;
}

.new-chat-btn {
  width: 100%;
  padding: 10px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  font-size: 14px;
  transition: opacity 0.2s ease, filter 0.2s ease;
}

.new-chat-btn:disabled,
.collapsed-new-chat-btn:disabled {
  cursor: not-allowed;
  opacity: 0.55;
  filter: grayscale(0.1);
}

.sidebar-collapsed-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 18px 12px;
}

.collapsed-new-chat-btn {
  width: 44px;
  height: 44px;
  border: none;
  border-radius: 12px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: #fff;
  font-size: 24px;
  line-height: 1;
  cursor: pointer;
}

.collapsed-chat-count {
  min-width: 44px;
  padding: 8px 10px;
  border-radius: 999px;
  background: #eef2ff;
  color: #4338ca;
  font-size: 12px;
  font-weight: 700;
  text-align: center;
}

.system-info-section {
  padding: 16px 20px;
  background: #f1f5f9;
}

.info-title {
  font-size: 12px;
  color: #64748b;
  margin-bottom: 8px;
}

.info-content {
  font-size: 12px;
  color: #475569;
}

.chat-history {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
}

.history-group + .history-group {
  margin-top: 14px;
}

.history-group-header {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
  padding: 8px 10px;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  background: #fff;
  color: #334155;
  cursor: pointer;
}

.history-group-header:hover {
  background: #f8fafc;
}

.history-group-title-wrap {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.history-group-toggle {
  color: #64748b;
  font-size: 12px;
}

.history-group-title {
  font-size: 13px;
  font-weight: 700;
  color: #0f172a;
}

.history-group-count {
  min-width: 24px;
  padding: 2px 8px;
  border-radius: 999px;
  background: #eef2ff;
  color: #4338ca;
  font-size: 12px;
  font-weight: 700;
  text-align: center;
}

.history-group-list {
  display: flex;
  flex-direction: column;
}

.empty-history-group {
  padding: 10px 12px;
  color: #94a3b8;
  font-size: 12px;
}

.history-item {
  padding: 12px;
  border: 1px solid transparent;
  border-radius: 10px;
  cursor: pointer;
  margin-bottom: 8px;
  background: #fff;
  transition: background-color 0.2s, border-color 0.2s, box-shadow 0.2s, opacity 0.2s;
}

.history-item:hover {
  background: #f8fafc;
  border-color: #e2e8f0;
}

.history-item.active {
  background: linear-gradient(180deg, #eef2ff 0%, #e0e7ff 100%);
  border-color: #a5b4fc;
  box-shadow: inset 0 0 0 1px rgba(79, 70, 229, 0.12);
}

.history-item.pinned {
  background: linear-gradient(180deg, #fffdf5 0%, #fff7db 100%);
  border-color: #f5d08a;
}

.history-item.active.pinned {
  background: linear-gradient(180deg, #fff8df 0%, #eef2ff 100%);
  border-color: #d4b14d;
  box-shadow: inset 0 0 0 1px rgba(212, 177, 77, 0.2);
}

.history-item.disabled {
  cursor: not-allowed;
  opacity: 0.68;
}

.history-item.disabled:hover {
  background: #fff;
  border-color: transparent;
  box-shadow: none;
}

.history-item.streaming {
  box-shadow: inset 0 0 0 1px rgba(37, 99, 235, 0.14);
}

.history-title {
  font-size: 14px;
  color: #1e293b;
  margin-bottom: 4px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.history-title-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.history-title-actions {
  display: flex;
  align-items: center;
  gap: 6px;
}

.pin-chat-btn {
  width: 24px;
  height: 24px;
  border: 1px solid #dbe4f0;
  border-radius: 999px;
  background: #fff;
  color: #94a3b8;
  font-size: 13px;
  line-height: 1;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: border-color 0.2s, color 0.2s, background-color 0.2s, opacity 0.2s;
}

.pin-chat-btn:hover:not(:disabled) {
  border-color: #f5b942;
  color: #d97706;
}

.pin-chat-btn.pinned {
  border-color: #f5b942;
  color: #d97706;
  background: #fff7db;
}

.pin-chat-btn:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.history-status-badge {
  padding: 2px 8px;
  border-radius: 999px;
  background: #dbeafe;
  color: #1d4ed8;
  font-size: 11px;
  font-weight: 700;
}

.history-stop-btn,
.history-delete-btn {
  border: 1px solid #dbe4f0;
  border-radius: 999px;
  background: #fff;
  color: #475569;
  font-size: 11px;
  font-weight: 600;
  line-height: 1;
  cursor: pointer;
  padding: 5px 9px;
  transition: border-color 0.2s, color 0.2s, background-color 0.2s, opacity 0.2s;
}

.history-stop-btn:hover:not(:disabled) {
  border-color: #93c5fd;
  background: #eff6ff;
  color: #1d4ed8;
}

.history-delete-btn:hover:not(:disabled) {
  border-color: #fecdd3;
  background: #fff1f2;
  color: #be123c;
}

.history-stop-btn:disabled,
.history-delete-btn:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.sync-icon {
  font-size: 12px;
  margin-left: 4px;
}

.sync-syncing {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.history-time {
  font-size: 12px;
  color: #94a3b8;
}

.empty-history {
  text-align: center;
  color: #94a3b8;
  padding: 20px;
}

.main-chat {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.panel-splitter {
  width: 10px;
  flex-shrink: 0;
  position: relative;
  cursor: col-resize;
  background: transparent;
}

.panel-splitter::before {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: 50%;
  width: 2px;
  transform: translateX(-50%);
  background: transparent;
  transition: background-color 0.2s ease;
}

.panel-splitter:hover::before {
  background: #cbd5e1;
}

.chat-header {
  padding: 16px 24px;
  background: white;
  border-bottom: 1px solid #e2e8f0;
}

.ai-icon {
  font-size: 32px;
}

.header-title h1 {
  font-size: 18px;
  color: #1e293b;
  margin: 0;
}

.messages-area {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
}

.empty-state {
  text-align: center;
  padding: 80px 20px;
  color: #64748b;
}

.empty-icon {
  font-size: 48px;
  margin-bottom: 16px;
}

.empty-title {
  font-size: 20px;
  color: #1e293b;
  margin-bottom: 8px;
}

.message {
  display: flex;
  gap: 12px;
  margin-bottom: 24px;
}

.message-question-anchor {
  scroll-margin-top: 20px;
}

.message-highlighted .message-content {
  box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.18);
  transition: box-shadow 0.2s ease;
}

.message-user {
  justify-content: flex-end;
}

.message-bot {
  justify-content: flex-start;
}

.message-content {
  max-width: 70%;
  padding: 12px 16px;
  border-radius: 12px;
  font-size: 14px;
  line-height: 1.6;
}

.message-user .message-content {
  background: #667eea;
  color: white;
}

.message-bot .message-content {
  background: white;
  border: 1px solid #e2e8f0;
}

.message-content :deep(.stream-bullet) {
  margin: 4px 0;
}

.message-content.message-graph-kb {
  background:
    linear-gradient(180deg, #ffffff 0%, #f8fbff 100%);
  border-color: #cfe0f6;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.72);
}

.message-content.message-graph-kb :deep(.graph-kb-markdown h2),
.message-content.message-graph-kb :deep(.graph-kb-markdown h3) {
  color: #0f3a69;
  font-weight: 700;
  letter-spacing: 0.01em;
}

.message-content.message-graph-kb :deep(.graph-kb-markdown h2) {
  margin: 4px 0 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid #d6e4f5;
  font-size: 16px;
}

.message-content.message-graph-kb :deep(.graph-kb-markdown h3) {
  margin: 16px 0 8px;
  font-size: 14px;
}

.message-content.message-graph-kb :deep(.graph-kb-markdown ul) {
  margin: 0 0 10px;
  padding-left: 20px;
}

.message-content.message-graph-kb :deep(.graph-kb-markdown li) {
  margin: 6px 0;
  color: #1f2937;
}

.message-content.message-graph-kb :deep(.graph-kb-markdown .doi-link) {
  color: #0f5cc0;
  font-weight: 600;
  text-decoration: underline;
  text-decoration-color: rgba(15, 92, 192, 0.28);
  text-underline-offset: 2px;
}

.message-content.message-patent {
  border-color: #dbe3ef;
  box-shadow: none;
}

.message-content.message-patent :deep(.patent-markdown h1, .patent-markdown h2, .patent-markdown h3) {
  margin: 14px 0 8px;
  padding-bottom: 0;
  border-bottom: none;
  color: #1f2937;
  font-weight: 700;
  line-height: 1.55;
}

.message-content.message-patent :deep(.patent-markdown h1) {
  font-size: 17px;
}

.message-content.message-patent :deep(.patent-markdown h2) {
  font-size: 16px;
}

.message-content.message-patent :deep(.patent-markdown h3) {
  font-size: 15px;
}

.message-content.message-patent :deep(.patent-markdown .patent-link) {
  color: #2563eb;
  font-weight: 500;
  text-decoration: underline;
  text-decoration-color: rgba(37, 99, 235, 0.26);
  text-underline-offset: 2px;
}

.message-content.message-patent :deep(.patent-markdown .patent-link:hover) {
  color: #1d4ed8;
  text-decoration-color: rgba(29, 78, 216, 0.48);
}

.bot-avatar {
  font-size: 24px;
}

.query-mode-badge {
  display: inline-block;
  padding: 4px 10px;
  background: #dbeafe;
  color: #1d4ed8;
  border-radius: 4px;
  font-size: 12px;
  margin-bottom: 8px;
}

.processing-steps {
  margin-bottom: 12px;
  padding: 4px 10px 10px;
}

.steps-panel {
  margin-bottom: 12px;
  border: 1px solid #dbe6f3;
  border-radius: 12px;
  background: linear-gradient(180deg, #f8fbff 0%, #f3f7fb 100%);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.8);
}

.steps-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  cursor: pointer;
  gap: 12px;
}

.steps-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #334155;
  white-space: nowrap;
  font-weight: 600;
}

.steps-meta {
  min-width: 0;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
}

.steps-toggle {
  color: #64748b;
}

.steps-count,
.steps-overview {
  background: #e2e8f0;
  color: #475569;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 11px;
}

.steps-overview {
  background: #e0ecff;
  color: #1d4ed8;
}

.stage-timing-summary {
  background: #f1f5f9;
  color: #334155;
  border: 1px solid rgba(148, 163, 184, 0.24);
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 11px;
  white-space: nowrap;
}

.stage-step-duration {
  display: inline-flex;
  align-items: center;
  margin-left: 6px;
  padding: 1px 6px;
  border-radius: 999px;
  background: #f1f5f9;
  color: #334155;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  line-height: 1.4;
  white-space: nowrap;
}

.patent-preview-panel {
  margin: 0 0 12px;
  padding: 12px;
  border: 1px solid #bfdbfe;
  border-radius: 12px;
  background: linear-gradient(180deg, #eff6ff 0%, #f8fbff 100%);
}

.patent-preview-header,
.patent-preview-stream-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.patent-preview-header {
  margin-bottom: 8px;
}

.patent-preview-title,
.patent-preview-stream-title {
  color: #1d4ed8;
  font-size: 13px;
  font-weight: 700;
}

.patent-preview-count,
.patent-preview-stream-status {
  padding: 2px 8px;
  border-radius: 999px;
  background: rgba(29, 78, 216, 0.08);
  color: #1d4ed8;
  font-size: 11px;
  font-weight: 600;
}

.patent-preview-pending {
  margin-bottom: 10px;
  color: #334155;
  font-size: 12px;
}

.patent-preview-stream {
  padding-top: 10px;
  border-top: 1px solid rgba(147, 197, 253, 0.45);
}

.patent-preview-stream:first-of-type {
  padding-top: 0;
  border-top: none;
}

.patent-preview-stream-body,
.patent-preview-stream-empty {
  margin-top: 8px;
  color: #334155;
  font-size: 13px;
  line-height: 1.6;
}

.patent-preview-stream-empty {
  color: #64748b;
}

.patent-preview-stream-body :deep(.markdown-renderer-patent-preview h1),
.patent-preview-stream-body :deep(.markdown-renderer-patent-preview h2),
.patent-preview-stream-body :deep(.markdown-renderer-patent-preview h3) {
  margin: 10px 0 6px;
  padding-bottom: 0;
  border-bottom: none;
  color: #1f2937;
  font-size: 14px;
  line-height: 1.55;
}

.patent-preview-stream-body :deep(.markdown-renderer-patent-preview .patent-link) {
  color: #2563eb;
  font-weight: 500;
  text-decoration: underline;
  text-decoration-color: rgba(37, 99, 235, 0.24);
  text-underline-offset: 2px;
}

.steps-summary {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  font-size: 12px;
  color: #334155;
  overflow: hidden;
}

.steps-summary .step-message {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 260px;
}

.step-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 0;
  font-size: 13px;
  border-top: 1px solid rgba(148, 163, 184, 0.16);
}

.step-item:first-child {
  border-top: none;
}

.step-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  min-width: 18px;
  height: 18px;
  margin-top: 2px;
  font-size: 12px;
}

.step-icon-processing {
  color: #2563eb;
}

.step-icon-success {
  color: #16a34a;
}

.step-icon-skipped {
  color: #64748b;
}

.step-icon-error {
  color: #dc2626;
}

.step-body {
  min-width: 0;
  flex: 1;
}

.step-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.step-title {
  color: #0f172a;
  font-weight: 600;
}

.step-message {
  color: #334155;
}

.step-detail {
  margin-top: 4px;
  color: #64748b;
  line-height: 1.5;
}

.step-error-text {
  margin-top: 4px;
  color: #b91c1c;
  line-height: 1.5;
}

.step-badge {
  background: #dcfce7;
  color: #166534;
  padding: 2px 6px;
  border-radius: 999px;
  font-size: 11px;
  margin-left: auto;
}

.loading-animation {
  color: #64748b;
  font-size: 14px;
}

.terminal-message-card,
.terminal-message-inline {
  margin-top: 10px;
  border-radius: 10px;
  padding: 10px 12px;
  border: 1px solid transparent;
}

.terminal-message-card {
  margin-top: 0;
}

.terminal-message-title {
  font-size: 13px;
  font-weight: 700;
}

.terminal-message-detail {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.5;
}

.terminal-message-failed {
  background: #fff1f2;
  border-color: #fecdd3;
  color: #9f1239;
}

.terminal-message-canceled {
  background: #fff7ed;
  border-color: #fed7aa;
  color: #9a3412;
}

.terminal-message-expired {
  background: #f8fafc;
  border-color: #cbd5e1;
  color: #475569;
}

.references-section {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid #e2e8f0;
}

.references-title {
  font-size: 13px;
  color: #475569;
  margin-bottom: 8px;
}

.reference-item {
  display: flex;
  gap: 8px;
  padding: 8px;
  background: #f8fafc;
  border-radius: 6px;
  margin-bottom: 6px;
  cursor: pointer;
}

.reference-item:hover {
  background: #f1f5f9;
}

.reference-index {
  color: #667eea;
  font-size: 12px;
}

.reference-title {
  font-size: 13px;
  color: #1e293b;
}

.reference-meta {
  font-size: 12px;
  color: #64748b;
  margin-top: 4px;
}

.reference-section {
  font-size: 12px;
  color: #475569;
  margin-top: 2px;
}

.doi-link {
  color: #667eea;
  text-decoration: none;
}

.ask-mode-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.ask-mode-label {
  font-size: 13px;
  color: #475569;
  font-weight: 600;
}

.ask-mode-group {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.ask-mode-btn {
  border: 1px solid #cbd5e1;
  background: #ffffff;
  color: #334155;
  border-radius: 999px;
  padding: 6px 12px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s ease;
}

.ask-mode-btn:hover:not(:disabled) {
  border-color: #64748b;
  color: #0f172a;
}

.ask-mode-btn.active {
  background: #0f172a;
  color: #ffffff;
  border-color: #0f172a;
}

.ask-mode-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.input-area {
  padding: 16px 24px;
  background: white;
  border-top: 1px solid #e2e8f0;
}

.input-wrapper {
  display: flex;
  gap: 12px;
  background: #f8fafc;
  border-radius: 12px;
  padding: 8px;
  border: 1px solid #e2e8f0;
}

.input-wrapper textarea {
  flex: 1;
  border: none;
  background: transparent;
  padding: 8px;
  font-size: 14px;
  resize: none;
  outline: none;
}

.send-btn {
  width: 40px;
  height: 40px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  font-size: 16px;
}

.send-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* PDF列表样式 */
.pdf-list-section {
  background: #f8f9fa;
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 16px;
}

.pdf-list-header {
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.pdf-list-toggle-btn {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 10px;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  background: #fff;
  color: #334155;
  cursor: pointer;
}

.pdf-list-toggle-btn:hover {
  background: #f8fafc;
}

.pdf-list-title {
  font-weight: 600;
  color: #333;
  font-size: 14px;
}

.pdf-list-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.pdf-action-btn {
  border: 1px solid #cbd5e1;
  background: #fff;
  color: #334155;
  border-radius: 6px;
  font-size: 12px;
  padding: 3px 8px;
  cursor: pointer;
}

.pdf-action-btn:hover {
  border-color: #94a3b8;
}

.pdf-select-tip {
  font-size: 12px;
  color: #64748b;
}

.pdf-list-items {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.pdf-list-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: rgba(255, 255, 255, 0.5);
  border-radius: 6px;
  transition: all 0.2s;
  position: relative;
}

.pdf-list-item.selected {
  border: 1px solid #93c5fd;
  background: #eff6ff;
}

.pdf-list-item-draft {
  border: 1px dashed #cbd5e1;
  background: #ffffff;
}

.pdf-list-item:hover {
  border-color: #4CAF50;
  box-shadow: 0 2px 4px rgba(76, 175, 80, 0.1);
}

.pdf-number {
  font-weight: 600;
  color: #4CAF50;
  font-size: 14px;
  min-width: 32px;
}

.file-select-wrap {
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.file-select-spacer {
  width: 14px;
  height: 14px;
  flex: 0 0 14px;
}

.file-select-wrap input[type="checkbox"] {
  width: 14px;
  height: 14px;
  cursor: pointer;
}

.pdf-title {
  flex: 1;
  color: #333;
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pdf-new-badge {
  background: #ff9800;
  color: white;
  padding: 2px 8px;
  border-radius: 12px;
  font-size: 11px;
  font-weight: 600;
}

.file-status-badge {
  padding: 2px 8px;
  border-radius: 12px;
  font-size: 11px;
  font-weight: 600;
  border: 1px solid transparent;
  white-space: nowrap;
}

.file-status-uploaded,
.file-status-parsing,
.file-status-indexing {
  background: #fff4db;
  color: #9a6700;
  border-color: #f1cf8f;
}

.file-status-parsed {
  background: #e8f3ff;
  color: #004b9a;
  border-color: #b8d6ff;
}

.file-status-ready {
  background: #e7f9ee;
  color: #0f6b3f;
  border-color: #9eddb8;
}

.file-status-failed {
  background: #ffecee;
  color: #a3132f;
  border-color: #f5b5bf;
}

.file-status-draft {
  background: #f1f5f9;
  color: #475569;
  border-color: #cbd5e1;
}

.file-download-btn {
  background: #eef4ff;
  border: 1px solid #c7d7fe;
  color: #1d4ed8;
  cursor: pointer;
  font-size: 12px;
  line-height: 1;
  font-weight: 600;
  border-radius: 999px;
  padding: 5px 10px;
  transition: background 0.2s ease, border-color 0.2s ease, color 0.2s ease;
}

.file-download-btn:hover {
  background: #dbeafe;
  border-color: #93c5fd;
  color: #1e40af;
}

.pdf-remove-btn {
  background: transparent;
  border: none;
  color: #64748b;
  cursor: pointer;
  font-size: 16px;
  line-height: 1;
}

.pdf-remove-btn:hover {
  color: #e11d48;
}

.pdf-remove-btn:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.pdf-delete-btn {
  background: #f44336;
  color: white;
  border: none;
  border-radius: 4px;
  width: 24px;
  height: 24px;
  cursor: pointer;
  font-size: 16px;
  line-height: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
}

.pdf-delete-btn:hover {
  background: #d32f2f;
  transform: scale(1.1);
}

.uploaded-pdf-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  background: #f0f9ff;
  border: 1px solid #bae6fd;
  border-radius: 8px;
  margin-bottom: 12px;
}

.pdf-info {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
}

.pdf-icon {
  font-size: 20px;
}

.pdf-name {
  font-size: 14px;
  color: #0c4a6e;
  font-weight: 500;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pdf-badge {
  padding: 2px 8px;
  background: #0ea5e9;
  color: white;
  border-radius: 4px;
  font-size: 11px;
}

.remove-pdf-btn {
  width: 24px;
  height: 24px;
  border: none;
  background: #ef4444;
  color: white;
  border-radius: 4px;
  cursor: pointer;
  font-size: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.2s;
}

.remove-pdf-btn:hover {
  background: #dc2626;
}

.upload-progress {
  padding: 12px 16px;
  background: #f8fafc;
  border-radius: 8px;
  margin-bottom: 12px;
}

.progress-bar {
  height: 6px;
  background: #e2e8f0;
  border-radius: 3px;
  overflow: hidden;
  margin-bottom: 8px;
}

.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
  transition: width 0.3s;
}

.progress-text {
  font-size: 12px;
  color: #64748b;
}

.upload-btn {
  width: 40px;
  height: 40px;
  background: #f1f5f9;
  color: #475569;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  font-size: 18px;
  transition: all 0.2s;
  display: flex;
  align-items: center;
  justify-content: center;
}

.upload-btn:hover:not(:disabled) {
  background: #e2e8f0;
}

.upload-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.system-message {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  background: #f0f9ff;
  border-left: 3px solid #0ea5e9;
  border-radius: 8px;
  margin: 12px auto;
  max-width: 80%;
  font-size: 14px;
  color: #0c4a6e;
}

.system-icon {
  font-size: 16px;
}

.system-text {
  flex: 1;
}

.question-outline {
  flex-shrink: 0;
  background: #ffffff;
  border-left: 1px solid #e2e8f0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.question-outline.collapsed {
  border-left-color: transparent;
}

.question-outline-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 18px 16px;
  border-bottom: 1px solid #e2e8f0;
}

.question-outline-title-group {
  min-width: 0;
}

.question-outline-title {
  font-size: 15px;
  font-weight: 600;
  color: #0f172a;
}

.question-outline-count {
  margin-top: 4px;
  font-size: 12px;
  color: #64748b;
}

.question-outline-toggle {
  border: 1px solid #cbd5e1;
  background: #fff;
  color: #334155;
  border-radius: 8px;
  padding: 6px 10px;
  font-size: 12px;
  cursor: pointer;
  white-space: nowrap;
}

.question-outline-toggle:hover {
  border-color: #94a3b8;
  background: #f8fafc;
}

.question-outline-body {
  flex: 1;
  overflow-y: auto;
  padding: 14px 12px 18px;
}

.question-outline-empty {
  font-size: 13px;
  color: #94a3b8;
  line-height: 1.6;
  padding: 8px 4px;
}

.question-outline-item {
  width: 100%;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid transparent;
  border-radius: 12px;
  background: #f8fafc;
  color: #1e293b;
  cursor: pointer;
  text-align: left;
  margin-bottom: 10px;
  transition: all 0.2s ease;
}

.question-outline-item:hover {
  background: #eef2ff;
  border-color: #c7d2fe;
}

.question-outline-item.active {
  background: #e0e7ff;
  border-color: #a5b4fc;
}

.outline-index {
  flex-shrink: 0;
  min-width: 28px;
  font-size: 12px;
  font-weight: 700;
  color: #4f46e5;
}

.outline-text {
  font-size: 13px;
  line-height: 1.5;
  color: #334155;
  word-break: break-word;
}

@media (max-width: 1280px) {
  .question-outline {
    width: 260px;
  }
}

@media (max-width: 1024px) {
  .sidebar {
    width: 240px !important;
  }

  .question-outline {
    display: none;
  }

  .panel-splitter {
    display: none;
  }

  .message-content {
    max-width: 78%;
  }
}

@media (max-width: 640px) {
  .steps-header {
    align-items: flex-start;
  }

  .steps-meta {
    flex-wrap: wrap;
  }
}
</style>
