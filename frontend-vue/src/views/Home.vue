<script setup>
import { ref, onMounted, onUnmounted, computed, nextTick, watch } from 'vue'
import { useChatStore } from '../stores/chatStore'
import { api } from '../services/api'
import { formatTime, formatAnswer } from '../utils'
import { createStreamingHtmlRenderer } from '../utils/streamingRender'
import { buildMessageRenderMemoKey } from '../utils/messageRenderMemo'
import { mergeSelectedFileIdsAfterUpload, resolveUploadedFileDisplayNumber } from '../utils/fileSelection'
import PdfReader from '../components/PdfReader.vue'
import { buildCitationLocationsForDoi } from '../utils/citationEvidence'
import { buildRoutingErrorMarkdown, getRouteModeLabel, mergeRoutingMetadata } from '../utils/routingStatus'

const PINNED_CHATS_COLLAPSED_KEY = 'lfp.sidebar.pinned-collapsed.v1'
const RECENT_CHATS_COLLAPSED_KEY = 'lfp.sidebar.recent-collapsed.v1'
const FILE_LIST_COLLAPSED_KEY = 'lfp.file-list.collapsed.v1'
const ASK_MODE_STORAGE_KEY = 'gateway.ask.mode.v1'
const ASK_MODE_LABELS = { fast: '快速模式', thinking: '思考模式', patent: '专利模式' }

const store = useChatStore()
const pdfReader = ref(null)
const appContainer = ref(null)
const inputMessage = ref('')
const messagesArea = ref(null)
const fileInput = ref(null)
const uploading = ref(false)
const uploadProgress = ref(0)
const streamingAbortController = ref(null)
const streamingChatId = ref('')
const streamingMessageRequestId = ref('')
const selectedFileIds = ref([])
const selectedAskMode = ref(localStorage.getItem(ASK_MODE_STORAGE_KEY) || 'thinking')
const leftSidebarCollapsed = ref(false)
const leftSidebarWidth = ref(280)
const leftSidebarLastExpandedWidth = ref(280)
let fileStatusPollTimer = null
let streamContentFlushFrame = null
let scrollFrame = null
let pendingStreamContent = ''
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
const highlightedQuestionMessageIndex = ref(null)
const userMessageElements = new Map()
const isPanelResizing = ref(false)
let questionHighlightTimer = null
let activeResizePanel = null
let documentClickHandler = null
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

const hasMessages = computed(() => store.currentMessages.length > 0)
const canSend = computed(() => inputMessage.value.trim() && !store.isStreaming)
const canToggleStreaming = computed(() => {
  if (store.isStreaming) return true
  return Boolean(canSend.value)
})
const askModeOptions = [
  { value: 'fast', label: '快速' },
  { value: 'thinking', label: '思考' },
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
const questionOutlineItems = computed(() => {
  let outlineIndex = 0
  return store.currentMessages.reduce((items, msg, messageIndex) => {
    if (msg?.role !== 'user') return items
    outlineIndex += 1
    items.push({
      outlineIndex,
      messageIndex,
      anchorId: getQuestionAnchorId(messageIndex),
      preview: getQuestionPreview(msg?.content || '')
    })
    return items
  }, [])
})
const kbSummaryText = computed(() => {
  if (store.kbInfo.loading) return '向量库: 加载中 | 知识图谱: 加载中'
  const vectorSize = Number(store.kbInfo.vectorSize ?? store.kbInfo.size ?? 0)
  const graphConnected = Boolean(store.kbInfo.graphConnected)
  const graphPart = graphConnected ? `${Number(store.kbInfo.graphSize ?? 0)} 条` : '未连接'
  return `向量库: ${vectorSize} 条 | 知识图谱: ${graphPart}`
})
const isHistoryLocked = computed(() => store.isStreaming && Boolean(streamingChatId.value))

const renderedMessageCache = new WeakMap()
const renderStreamingMessageHtml = createStreamingHtmlRenderer()

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

function startStreamingSession(chatId, requestId) {
  streamingChatId.value = normalizeChatId(chatId)
  streamingMessageRequestId.value = String(requestId || '').trim()
}

function clearStreamingSession() {
  streamingChatId.value = ''
  streamingMessageRequestId.value = ''
}

function getStreamingTargetMessage() {
  const chat = getChatById(streamingChatId.value)
  if (!chat || !Array.isArray(chat.messages) || chat.messages.length === 0) return null

  const requestId = String(streamingMessageRequestId.value || '').trim()
  if (requestId) {
    for (let index = chat.messages.length - 1; index >= 0; index -= 1) {
      const message = chat.messages[index]
      if ((message?.role === 'assistant' || message?.role === 'bot') && message?.streamRequestId === requestId) {
        return { chat, message, index }
      }
    }
  }

  for (let index = chat.messages.length - 1; index >= 0; index -= 1) {
    const message = chat.messages[index]
    if (message?.role === 'assistant' || message?.role === 'bot') {
      return { chat, message, index }
    }
  }

  return null
}

function updateStreamingTargetMessage(updates) {
  const target = getStreamingTargetMessage()
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

function isStreamingChat(chatId) {
  return isHistoryLocked.value && normalizeChatId(chatId) === streamingChatId.value
}

function isHistoryItemDisabled(chatId) {
  const normalizedChatId = normalizeChatId(chatId)
  if (!isHistoryLocked.value) return false
  return normalizedChatId !== streamingChatId.value
}

function getHistoryItemTitle(chatId) {
  if (isHistoryItemDisabled(chatId)) {
    return '回答生成中，当前不能切换到其他会话'
  }
  if (isStreamingChat(chatId)) {
    return '当前会话正在生成回答'
  }
  return ''
}

function setAskMode(mode) {
  selectedAskMode.value = normalizeAskMode(mode)
  localStorage.setItem(ASK_MODE_STORAGE_KEY, selectedAskMode.value)
}

function formatQueryModeLabel(mode) {
  const key = String(mode || '').trim().toLowerCase()
  return ASK_MODE_LABELS[key] || String(mode || '').trim()
}

function getQuestionAnchorId(messageIndex) {
  return `question-${messageIndex}`
}

function getQuestionPreview(content) {
  const normalized = String(content || '').replace(/\s+/g, ' ').trim()
  if (!normalized) return '未命名问题'
  return normalized.length > 48 ? `${normalized.slice(0, 48)}...` : normalized
}

function setUserMessageElement(messageIndex, el) {
  if (el) {
    userMessageElements.set(messageIndex, el)
    return
  }
  userMessageElements.delete(messageIndex)
}

function clearQuestionHighlight() {
  highlightedQuestionMessageIndex.value = null
  if (questionHighlightTimer !== null) {
    window.clearTimeout(questionHighlightTimer)
    questionHighlightTimer = null
  }
}

function resetQuestionOutlineState() {
  clearQuestionHighlight()
  userMessageElements.clear()
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

function scrollToQuestion(item) {
  if (!item) return
  const target = userMessageElements.get(item.messageIndex)
  if (!target) return
  target.scrollIntoView({ behavior: 'smooth', block: 'start' })
  highlightedQuestionMessageIndex.value = item.messageIndex
  if (questionHighlightTimer !== null) {
    window.clearTimeout(questionHighlightTimer)
  }
  questionHighlightTimer = window.setTimeout(() => {
    highlightedQuestionMessageIndex.value = null
    questionHighlightTimer = null
  }, 1800)
}

function isStepsCollapsed(msg) {
  return msg?.stepsCollapsed === true
}

function toggleSteps(index) {
  const msg = store.currentMessages[index]
  if (!msg) return
  msg.stepsCollapsed = !isStepsCollapsed(msg)
}

function getLastStep(msg) {
  if (!msg?.steps || msg.steps.length === 0) return null
  return msg.steps[msg.steps.length - 1]
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
  if (status === 'error') return '●'
  return '●'
}

function getStepTitle(step) {
  if (!step) return '处理中'
  return String(step.title || splitStepMessage(step.message).title || step.message || '处理中')
}

function getStepDetail(step) {
  if (!step) return ''
  const detail = String(step.detail || '').trim()
  if (detail) return detail
  const title = getStepTitle(step)
  const message = String(step.message || '').trim()
  if (!message || message === title) return ''
  return message
}

function getStepCount(step) {
  const count = Number(step?.data?.count)
  return Number.isFinite(count) && count > 0 ? count : null
}

function getStepOverview(msg) {
  const steps = Array.isArray(msg?.steps) ? msg.steps : []
  if (steps.length === 0) return ''
  const processing = steps.filter((step) => normalizeStepStatus(step?.status) === 'processing').length
  const success = steps.filter((step) => normalizeStepStatus(step?.status) === 'success').length
  const error = steps.filter((step) => normalizeStepStatus(step?.status) === 'error').length
  if (error > 0) return `失败 ${error} · 完成 ${success}`
  if (processing > 0) return `进行中 ${processing} · 完成 ${success}`
  return `已完成 ${success}`
}

function getCollapsedStepSummary(msg) {
  const current = Array.isArray(msg?.steps)
    ? [...msg.steps].reverse().find((step) => normalizeStepStatus(step?.status) === 'processing')
    : null
  return current || getLastStep(msg)
}

function upsertStreamingStep(stepPayload, activeStepKey, { markPreviousActiveSuccess = false } = {}) {
  return updateStreamingSteps((steps) => {
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
  if (['success', 'succeeded', 'completed', 'complete', 'done', 'ok'].includes(raw)) return 'success'
  if (['error', 'failed', 'fail', 'failure'].includes(raw)) return 'error'
  return fallback
}

function updateStreamingSteps(mutator) {
  const target = getStreamingTargetMessage()
  if (!target?.message) return []
  const steps = Array.isArray(target.message.steps) ? [...target.message.steps] : []
  mutator(steps)
  updateStreamingTargetMessage({ steps })
  return steps
}

function getRenderedMessageHtml(msg) {
  const content = String(msg?.content || '')
  const referenceLinks = Array.isArray(msg?.referenceLinks) ? msg.referenceLinks : []
  const cached = renderedMessageCache.get(msg)
  if (cached && cached.content === content && cached.referenceLinks === referenceLinks) {
    return cached.html
  }
  const html = formatAnswer(content, referenceLinks)
  renderedMessageCache.set(msg, { content, referenceLinks, html })
  return html
}

function isStreamingTextMessage(msg) {
  if (!msg) return false
  if (!(msg.role === 'bot' || msg.role === 'assistant')) return false
  return store.isStreaming && msg.isComplete !== true
}

function getStreamingMessageHtml(msg) {
  return renderStreamingMessageHtml(msg)
}

function getMessageRenderMemoKey(msg) {
  return buildMessageRenderMemoKey(msg)
}

function getFallbackQueryModeLabel(data, existingMeta = {}) {
  const modeRaw = String(data?.query_mode || data?.queryMode || '').trim()
  if (modeRaw) return formatQueryModeLabel(modeRaw)
  return getRouteModeLabel(data?.route || existingMeta?.route || '')
}

function flushPendingStreamContent() {
  if (!pendingStreamContent) return
  const target = getStreamingTargetMessage()
  const existingContent = String(target?.message?.content || '')
  updateStreamingTargetMessage({ content: existingContent + pendingStreamContent })
  pendingStreamContent = ''
  scrollToBottom()
}

function scheduleStreamContentFlush() {
  if (streamContentFlushFrame !== null) return
  streamContentFlushFrame = window.requestAnimationFrame(() => {
    streamContentFlushFrame = null
    flushPendingStreamContent()
  })
}

function resetStreamFlushState() {
  pendingStreamContent = ''
  if (streamContentFlushFrame !== null) {
    window.cancelAnimationFrame(streamContentFlushFrame)
    streamContentFlushFrame = null
  }
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
  
  if (store.chats.length === 0) {
    store.createChat()
  } else {
    await store.switchChat(store.currentChatId || store.chats[0].id)
  }

  documentClickHandler = (e) => {
    const target = e.target
    if (target.classList && target.classList.contains('doi-link')) {
      e.preventDefault()
      const doi = target.getAttribute('data-doi')

      const messageElement = target.closest('.message[data-message-index]')
      const messageIndex = Number(messageElement?.dataset?.messageIndex || -1)
      const currentMsg = Number.isInteger(messageIndex) && messageIndex >= 0
        ? store.currentMessages[messageIndex]
        : null
      const locations = buildCitationLocationsForDoi({
        doi,
        doiLocations: currentMsg?.doiLocations || {},
        references: currentMsg?.references || []
      })

      if (doi && pdfReader.value) {
        pdfReader.value.openReader(doi, locations)
      }
    }
  }
  document.addEventListener('click', documentClickHandler)

  if (hasPendingFileProcessing()) {
    startFileStatusPolling()
  }
  clampPanelWidths()
  window.addEventListener('resize', clampPanelWidths)
})

onUnmounted(() => {
  if (store.isStreaming) {
    stopStreaming()
  }
  stopFileStatusPolling()
  stopPanelResize()
  resetStreamFlushState()
  resetQuestionOutlineState()
  if (scrollFrame !== null) {
    window.cancelAnimationFrame(scrollFrame)
    scrollFrame = null
  }
  window.removeEventListener('resize', clampPanelWidths)
  if (documentClickHandler) {
    document.removeEventListener('click', documentClickHandler)
    documentClickHandler = null
  }
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
  if (store.isStreaming) return
  stopFileStatusPolling()
  clearSelectedFiles()
  resetQuestionOutlineState()
  store.createChat()
  inputMessage.value = ''
}

async function switchChat(chatId) {
  const nextChatId = normalizeChatId(chatId)
  if (!nextChatId || nextChatId === normalizeChatId(store.currentChatId)) return
  if (store.isStreaming) return

  const requestSeq = ++switchChatRequestSeq
  stopFileStatusPolling()
  clearSelectedFiles()
  resetQuestionOutlineState()
  await store.switchChat(nextChatId)
  if (requestSeq !== switchChatRequestSeq) return
  if (hasPendingFileProcessing()) {
    startFileStatusPolling()
  }
}

function handleHistoryItemClick(chatId) {
  if (isHistoryItemDisabled(chatId)) return
  void switchChat(chatId)
}

function deleteChat(chatId) {
  if (confirm('确定要删除这个对话吗？')) {
    store.deleteChat(chatId)
  }
}

function toggleChatPinned(chatId) {
  if (store.isStreaming) return
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

function getLatestBotMessage() {
  const messages = Array.isArray(store.currentMessages) ? [...store.currentMessages].reverse() : []
  return messages.find(msg => msg?.role === 'bot' || msg?.role === 'assistant') || null
}

function getLastFocusFileIds() {
  const latestBot = getLatestBotMessage()
  if (!latestBot) return []
  const usedFiles = latestBot?.metadata?.used_files
  if (!Array.isArray(usedFiles) || usedFiles.length === 0) return []
  const uniqueIds = []
  const seen = new Set()
  usedFiles.forEach((item) => {
    const id = Number(item?.file_id || 0)
    if (!id || seen.has(id)) return
    seen.add(id)
    uniqueIds.push(id)
  })
  if (uniqueIds.length > 0) return uniqueIds
  return []
}

function getLastTurnRoute() {
  const latestBot = getLatestBotMessage()
  if (!latestBot) return ''
  return String(latestBot?.metadata?.route || '').trim().toLowerCase()
}

async function sendMessage() {
  if (!canSend.value) {
    if (store.isStreaming) stopStreaming()
    return
  }

  const message = inputMessage.value.trim()
  if (!message) return

  const chat = store.currentChat
  if (!chat) return

  await store.addUserMessage(message)
  inputMessage.value = ''
  scrollToBottom()

  const streamRequestId = `stream_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
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
  })
  const streamChatId = normalizeChatId(store.currentChatId || chat.id)
  startStreamingSession(streamChatId, streamRequestId)
  scrollToBottom()

  store.setStreaming(true)
  streamingAbortController.value = new AbortController()

  try {
    const chatHistory = store.currentMessages
      .filter(m => String(m?.content || '').trim().length > 0)
      .slice(-10)
      .map(m => ({ role: m.role, content: m.content }))

    // 传递 conversationId 到 askStream
    // 注意：userId 从后端 JWT token 中获取，不需要前端传递
    const conversationId = store.currentChat.synced ? parseInt(store.currentChat.id) : null

    // 构建文件上下文（PDF + Excel/CSV）
    const pdfContext = {
      newly_uploaded_ids: store.getNewlyUploadedFileIds(),
      all_available_ids: store.getAllUploadedFileIds(),
      selected_ids: [...selectedFileIds.value],
      last_focus_ids: getLastFocusFileIds(),
      last_turn_route: getLastTurnRoute()
    }

    let thinkingIndex = 0
    let activeStepKey = ''

    const markActiveStep = (status, error = '') => {
      if (!activeStepKey) return
      updateStreamingSteps((steps) => {
        const idx = steps.findIndex((step) => step.step === activeStepKey)
        if (idx < 0) return
        steps[idx] = {
          ...steps[idx],
          status: normalizeStepStatus(status, steps[idx].status || 'processing'),
          ...(error ? { error: String(error) } : {}),
          updatedAt: new Date().toISOString()
        }
      })
    }

    for await (const data of api.askStream(
      message,
      chatHistory,
      conversationId,
      pdfContext,
      streamingAbortController.value?.signal,
      selectedAskMode.value
    )) {
      if (data.type === 'thinking') {
        flushPendingStreamContent()
        const thinkingMessage = String(data.content || data.message || '').trim()
        if (thinkingMessage) {
          thinkingIndex += 1
          const stepPayload = buildStepPayload(data, `thinking_${thinkingIndex}`, 'processing')
          upsertStreamingStep(stepPayload, activeStepKey, { markPreviousActiveSuccess: true })
          activeStepKey = stepPayload.step
        }
      } else if (data.type === 'step') {
        flushPendingStreamContent()
        const stepPayload = buildStepPayload(data, `step_${Date.now()}`, 'processing')
        upsertStreamingStep(stepPayload, activeStepKey, {
          markPreviousActiveSuccess:
            stepPayload.step !== activeStepKey && normalizeStepStatus(stepPayload.status) === 'processing'
        })
        activeStepKey = stepPayload.step
      } else if (data.type === 'metadata') {
        const targetMessage = getStreamingTargetMessage()?.message || {}
        const existingMeta = (targetMessage.metadata && typeof targetMessage.metadata === 'object') ? targetMessage.metadata : {}
        const mergedMeta = mergeRoutingMetadata(existingMeta, data)
        const modeFromExpert = data.expert === 'neo4j'
          ? '知识图谱'
          : data.expert === 'community'
            ? '社区分析'
            : data.expert === 'tabular'
              ? '表格问答'
              : '文献检索'
        updateStreamingTargetMessage({
          expert: data.expert,
          queryMode: getFallbackQueryModeLabel(data, mergedMeta) || modeFromExpert,
          metadata: mergedMeta
        })
      } else if (data.type === 'content') {
        pendingStreamContent += String(data.content || '')
        scheduleStreamContentFlush()
      } else if (data.type === 'done') {
        flushPendingStreamContent()
        const targetMessage = getStreamingTargetMessage()?.message || {}
        const existingMeta = (targetMessage.metadata && typeof targetMessage.metadata === 'object') ? targetMessage.metadata : {}
        const doneMeta = (data.metadata && typeof data.metadata === 'object') ? data.metadata : {}
        const referenceLinks = data.reference_links || data.pdf_links || data.referenceLinks || data.pdfLinks || []
        const references = Array.isArray(data.reference_objects)
          ? data.reference_objects
          : (Array.isArray(data.references) ? data.references : [])
        const mergedMeta = mergeRoutingMetadata({ ...existingMeta, ...doneMeta }, data)
        const finalizedSteps = updateStreamingSteps((steps) => {
          if (activeStepKey) {
            const activeIdx = steps.findIndex((step) => step.step === activeStepKey)
            if (activeIdx >= 0) {
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
          used_files: Array.isArray(data.used_files) ? data.used_files : (existingMeta.used_files || []),
          timings: (data.timings && typeof data.timings === 'object') ? data.timings : (existingMeta.timings || {}),
        }
        if (!targetMessage.queryMode) {
          updates.queryMode = getFallbackQueryModeLabel(data, mergedMeta)
        }

        updateStreamingTargetMessage(updates)
      } else if (data.type === 'error') {
        flushPendingStreamContent()
        const targetMessage = getStreamingTargetMessage()?.message || {}
        const existingMeta = (targetMessage.metadata && typeof targetMessage.metadata === 'object') ? targetMessage.metadata : {}
        const mergedMeta = mergeRoutingMetadata(existingMeta, data)
        const errorText = String(data.message || data.error || '处理失败')
        const renderedError = buildRoutingErrorMarkdown({
          code: data.code,
          message: errorText,
          metadata: mergedMeta,
        })
        if (activeStepKey) {
          markActiveStep('error', errorText)
        } else {
          updateStreamingSteps((steps) => {
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
        updateStreamingTargetMessage({
          content: existingContent ? `${existingContent}\n\n${renderedError}` : renderedError,
          queryMode: getFallbackQueryModeLabel(data, mergedMeta) || targetMessage.queryMode || '',
          metadata: mergedMeta,
          isComplete: true
        })
      }
    }
  } catch (e) {
    flushPendingStreamContent()
    if (e?.name === 'AbortError') {
      return
    }
    const targetMessage = getStreamingTargetMessage()?.message || {}
    const existingMeta = (targetMessage.metadata && typeof targetMessage.metadata === 'object') ? targetMessage.metadata : {}
    const payload = (e?.payload && typeof e.payload === 'object') ? e.payload : {}
    const mergedMeta = mergeRoutingMetadata(existingMeta, payload)
    const errorMessage = String(payload?.message || e?.message || '未知错误')
    const renderedError = buildRoutingErrorMarkdown({
      code: payload?.code,
      message: errorMessage,
      metadata: mergedMeta,
    })
    updateStreamingTargetMessage({
      content: renderedError,
      queryMode: getFallbackQueryModeLabel(payload, mergedMeta) || targetMessage.queryMode || '',
      metadata: mergedMeta,
      isComplete: true
    })
  } finally {
    streamingAbortController.value = null
    resetStreamFlushState()
    store.setStreaming(false)
    clearStreamingSession()
    scrollToBottom()
  }
}

function stopStreaming() {
  streamingAbortController.value?.abort()
  streamingAbortController.value = null
  flushPendingStreamContent()
  resetStreamFlushState()
  const targetMessage = getStreamingTargetMessage()?.message
  const existingContent = String(targetMessage?.content || '')
  updateStreamingTargetMessage({
    content: existingContent.includes('[对话已中断]') ? existingContent : `${existingContent}\n\n[对话已中断]`,
    isComplete: true
  })
  store.setStreaming(false)
  clearStreamingSession()
}

function scrollToBottom() {
  if (scrollFrame !== null) return
  scrollFrame = window.requestAnimationFrame(() => {
    scrollFrame = null
    nextTick(() => {
      if (messagesArea.value) {
        messagesArea.value.scrollTop = messagesArea.value.scrollHeight
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

async function handleFileSelect(event) {
  const file = event.target.files?.[0]
  if (!file) return
  
  // 检查文件类型
  const fileName = file.name.toLowerCase()
  const isPdf = fileName.endsWith('.pdf')
  const isExcel = fileName.endsWith('.xlsx') || fileName.endsWith('.xls') || fileName.endsWith('.csv')
  
  if (!isPdf && !isExcel) {
    alert('只支持PDF、Excel和CSV文件')
    return
  }
  
  // 文件大小检查由后端配额系统处理
  uploading.value = true
  uploadProgress.value = 0
  const uploadConversationTitle = store.buildAutoTitleFromFileName(file.name)
  
  try {
    // 如果对话未同步到服务器，先创建对话
    if (!store.currentChat.synced) {
      const userStr = localStorage.getItem('user')
      if (userStr) {
        const user = JSON.parse(userStr)
        const title = uploadConversationTitle
        
        try {
          const response = await api.createConversation(user.id, title)
          
          // 更新对话ID和同步状态
          const oldId = store.currentChat.id
          const chatIndex = store.chats.findIndex(c => c.id === oldId)
          if (chatIndex !== -1) {
            const newId = response.conversation_id.toString()
            store.chats[chatIndex].id = newId
            store.chats[chatIndex].title = response.title || title
            store.chats[chatIndex].synced = true
            store.currentChatId = newId
            store.persistLocalState()
          }
        } catch (e) {
          alert('创建对话失败: ' + e.message)
          uploading.value = false
          return
        }
      }
    }
    
    // 获取当前对话ID
    const conversationId = store.currentChat.synced ? parseInt(store.currentChat.id) : null
    
    if (!conversationId) {
      alert('无法获取对话ID，请刷新页面重试')
      uploading.value = false
      return
    }
    
    let result
    
    if (isPdf) {
      // 上传PDF
      result = await api.uploadPdf(file, conversationId, (progress) => {
        uploadProgress.value = progress
      })
      
      if (result.success) {
        store.addUploadedPdf({
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
        })
        const uploadedFileId = Number(result?.document?.file_id || 0)
        if (uploadedFileId > 0) {
          selectedFileIds.value = mergeSelectedFileIdsAfterUpload(selectedFileIds.value, uploadedFileId)
        }
        const parsedTitle = String(result?.document?.title || '').trim()
        if (parsedTitle && store.currentChat?.title === uploadConversationTitle) {
          await store.updateCurrentChatTitle(parsedTitle, { persist: true })
        }
        store.addSystemMessage(`✅ PDF上传成功: ${result.document.title || file.name} (#${resolveUploadedFileDisplayNumber(result.document)})`)
        inputMessage.value = `请帮我总结一下这篇文献的主要内容`
        startFileStatusPolling()
      }
    } else if (isExcel) {
      // 上传Excel/CSV
      result = await store.uploadExcel(file)
      
      if (result) {
        const uploadedFileId = Number(result?.file_id || 0)
        if (uploadedFileId > 0) {
          selectedFileIds.value = mergeSelectedFileIdsAfterUpload(selectedFileIds.value, uploadedFileId)
        }
        if (store.currentChat?.title === '新对话') {
          await store.updateCurrentChatTitle(uploadConversationTitle, { persist: true, onlyIfPlaceholder: true })
        }
        store.addSystemMessage(`✅ Excel上传成功: ${result.title || file.name} (#${resolveUploadedFileDisplayNumber(result)})`)
        inputMessage.value = `请帮我分析一下这个表格的数据`
        result = { success: true }  // 标记为成功
        startFileStatusPolling()
      } else {
        result = { success: false, error: '上传失败' }
      }
    }
    
    if (!result || (result.success === false)) {
      alert('上传失败: ' + (result?.error || '未知错误'))
    }
  } catch (error) {
    alert('上传失败: ' + error.message)
  } finally {
    uploading.value = false
    uploadProgress.value = 0
    // 清空文件输入
    if (fileInput.value) {
      fileInput.value.value = ''
    }
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
          <button class="new-chat-btn" type="button" :disabled="store.isStreaming" @click="createNewChat">新建对话</button>
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
                      <span v-if="isStreamingChat(chat.id)" class="history-status-badge">生成中</span>
                      <button
                        class="pin-chat-btn"
                        :class="{ pinned: chat.isPinned }"
                        type="button"
                        :disabled="store.isStreaming"
                        :title="store.isStreaming ? '生成中不可置顶/取消置顶' : (chat.isPinned ? '取消置顶' : '置顶对话')"
                        @click.stop="toggleChatPinned(chat.id)"
                      >
                        {{ chat.isPinned ? '★' : '☆' }}
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
                      <span v-if="isStreamingChat(chat.id)" class="history-status-badge">生成中</span>
                      <button
                        class="pin-chat-btn"
                        :class="{ pinned: chat.isPinned }"
                        type="button"
                        :disabled="store.isStreaming"
                        :title="store.isStreaming ? '生成中不可置顶/取消置顶' : (chat.isPinned ? '取消置顶' : '置顶对话')"
                        @click.stop="toggleChatPinned(chat.id)"
                      >
                        {{ chat.isPinned ? '★' : '☆' }}
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
        <button class="collapsed-new-chat-btn" type="button" :disabled="store.isStreaming" @click="createNewChat" title="新建对话">＋</button>
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
            <div class="kb-info">{{ kbSummaryText }}</div>
          </div>
        </div>
        <div class="header-right">
          <a href="/profile" class="nav-link">个人中心</a>
          <a href="/admin" class="nav-link admin-only">管理后台</a>
        </div>
      </header>

      <!-- 文件列表显示 (PDF + Excel统一编号) -->
      <div v-if="(store.currentChat?.pdf_list?.length > 0) || (store.currentChat?.excel_list?.length > 0)" class="pdf-list-section">
        <div class="pdf-list-header">
          <button class="pdf-list-toggle-btn" type="button" @click="toggleFileListSection">
            <span class="history-group-title-wrap">
              <span class="history-group-toggle">{{ fileListCollapsed ? '▶' : '▼' }}</span>
              <span class="pdf-list-title">📎 已上传的文件</span>
            </span>
            <span class="history-group-count">{{ (store.currentChat.pdf_list?.length || 0) + (store.currentChat.excel_list?.length || 0) }}</span>
          </button>
          <div class="pdf-list-actions">
            <button class="pdf-action-btn" @click="selectAllFiles">全选</button>
            <button class="pdf-action-btn" @click="clearSelectedFiles">清空</button>
            <span class="pdf-select-tip">已选 {{ selectedFileIds.length }} 个（不选则自动判定）</span>
          </div>
        </div>
        <div v-show="!fileListCollapsed" class="pdf-list-items">
          <!-- 合并并按file_id排序显示 -->
          <template v-for="file in mergedAndSortedFiles" :key="file.type + '-' + file.id">
            <div
              class="pdf-list-item"
              @click="toggleFileSelection(file.file_id)"
              :class="{ selected: isFileSelected(file.file_id) }"
            >
              <label class="file-select-wrap" title="选择文件参与本轮问答" @click.stop>
                <input
                  type="checkbox"
                  :checked="isFileSelected(file.file_id)"
                  @change="toggleFileSelection(file.file_id)"
                >
              </label>
              <span class="pdf-number">#{{ file.display_no || file.file_no || file.file_id }}</span>
              <span class="pdf-icon">{{ file.type === 'pdf' ? '📄' : '📊' }}</span>
              <span class="pdf-title">{{ file.title }}</span>
              <span
                class="file-status-badge"
                :class="fileStatusClass(file)"
                :title="file.last_error || fileStatusLabel(file)"
              >
                {{ fileStatusLabel(file) }}
              </span>
              <span v-if="store.sessionState.newlyUploadedPdfIds.includes(file.file_id)" class="pdf-new-badge">新</span>
              <button class="file-download-btn" @click.stop="downloadUploadedFile(file)" title="下载文件">下载</button>
              <button class="pdf-remove-btn" @click.stop="file.type === 'pdf' ? handleRemovePdf(file.file_id) : handleRemoveExcel(file.file_id)" title="删除">×</button>
            </div>
          </template>
        </div>
      </div>

      <div class="messages-area" ref="messagesArea">
        <template v-if="!hasMessages">
          <div class="empty-state">
            <div class="empty-icon">🔋</div>
            <div class="empty-title">你好！我是磷酸铁锂材料专家</div>
            <div>请提出您的问题</div>
          </div>
        </template>
        <template v-else>
          <div
            v-for="(msg, index) in store.currentMessages"
            :key="index"
            v-memo="[getMessageRenderMemoKey(msg), highlightedQuestionMessageIndex === index]"
            class="message"
            :data-message-index="index"
            :class="[
              'message-' + msg.role,
              {
                'message-question-anchor': msg.role === 'user',
                'message-highlighted': highlightedQuestionMessageIndex === index
              }
            ]"
            :id="msg.role === 'user' ? getQuestionAnchorId(index) : undefined"
            :ref="msg.role === 'user' ? (el) => setUserMessageElement(index, el) : null"
          >
            <template v-if="msg.role === 'user'">
              <div class="message-content">{{ msg.content }}</div>
            </template>
            <template v-else-if="msg.role === 'system'">
              <div class="system-message">
                <span class="system-icon">ℹ️</span>
                <span class="system-text">{{ msg.content }}</span>
              </div>
            </template>
            <template v-else-if="msg.role === 'bot' || msg.role === 'assistant'">
              <div class="bot-avatar">✨</div>
              <div class="message-content">
                <div v-if="msg.queryMode" class="query-mode-badge">{{ msg.queryMode }}</div>
                <div v-if="msg.steps && msg.steps.length > 0" class="steps-panel">
                  <div class="steps-header" @click="toggleSteps(index)">
                    <div class="steps-title">
                      <span class="steps-toggle">{{ isStepsCollapsed(msg) ? '▶' : '▼' }}</span>
                      <span>处理过程</span>
                      <span class="steps-count">{{ msg.steps.length }}</span>
                    </div>
                    <div class="steps-meta">
                      <span v-if="getStepOverview(msg)" class="steps-overview">{{ getStepOverview(msg) }}</span>
                      <div v-if="isStepsCollapsed(msg) && getCollapsedStepSummary(msg)" class="steps-summary">
                        <span class="step-icon" :class="'step-icon-' + normalizeStepStatus(getCollapsedStepSummary(msg).status)">
                          {{ getStepIcon(getCollapsedStepSummary(msg)) }}
                        </span>
                        <span class="step-message">{{ getStepTitle(getCollapsedStepSummary(msg)) }}</span>
                        <span v-if="getStepCount(getCollapsedStepSummary(msg))" class="step-badge">
                          {{ getStepCount(getCollapsedStepSummary(msg)) }}
                        </span>
                      </div>
                    </div>
                  </div>
                  <div v-show="!isStepsCollapsed(msg)" class="processing-steps">
                    <div v-for="(step, idx) in msg.steps" :key="step.step || idx" class="step-item" :class="'step-' + normalizeStepStatus(step.status)">
                      <span class="step-icon" :class="'step-icon-' + normalizeStepStatus(step.status)">{{ getStepIcon(step) }}</span>
                      <div class="step-body">
                        <div class="step-row">
                          <span class="step-title">{{ getStepTitle(step) }}</span>
                          <span v-if="getStepCount(step)" class="step-badge">{{ getStepCount(step) }}</span>
                        </div>
                        <div v-if="getStepDetail(step)" class="step-detail">{{ getStepDetail(step) }}</div>
                        <div v-if="step.error" class="step-error-text">{{ step.error }}</div>
                      </div>
                    </div>
                  </div>
                </div>
                <div v-if="msg.content && isStreamingTextMessage(msg)" v-html="getStreamingMessageHtml(msg)"></div>
                <div v-else-if="msg.content" v-html="getRenderedMessageHtml(msg)"></div>
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
              :disabled="store.isStreaming"
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
            :disabled="uploading || store.isStreaming"
            title="上传文件（PDF/Excel/CSV）"
          >
            📎
          </button>
          <textarea v-model="inputMessage" placeholder="问我任何关于磷酸铁锂的问题..." rows="1" @keydown.enter.prevent="sendMessage" @input="autoResize($event)"></textarea>
          <button class="send-btn" :disabled="!canToggleStreaming" @click="sendMessage">{{ store.isStreaming ? '⏹' : '➤' }}</button>
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
          :class="{ active: highlightedQuestionMessageIndex === item.messageIndex }"
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

.kb-info {
  font-size: 13px;
  color: #64748b;
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
</style>
