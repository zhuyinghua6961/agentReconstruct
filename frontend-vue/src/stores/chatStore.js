import { defineStore } from 'pinia'
import { prepareChatsForPersistence, restorePersistedChats } from './chatPersistence.js'
import { resolveChatPersistPolicy, shouldForcePersistForStreamingTransition } from './streamPersistPolicy.js'
import { ref, computed } from 'vue'
import { api } from '../services/api.js'

export const useChatStore = defineStore('chat', () => {
  const DEFAULT_CHAT_TITLE = '新对话'
  const CHATS_STORAGE_KEY = 'lfp_chats'
  const ACTIVE_CHAT_STORAGE_KEY = 'lfp_current_chat_id'

  // 状态
  const chats = ref([])
  const currentChatId = ref(null)
  const isStreaming = ref(false)
  const kbInfo = ref({
    loading: true,
    size: 0,
    vectorSize: 0,
    graphSize: 0,
    graphConnected: false,
  })
  const userId = ref(null)
  const syncStatus = ref('synced') // synced/syncing/failed
  let persistTimer = null
  let switchChatRequestToken = 0
  
  // PDF会话状态追踪
  const sessionState = ref({
    initialPdfIds: [],      // 打开对话时已有的PDF ID
    newlyUploadedPdfIds: [], // 本次会话新上传的PDF ID
    lastUsedPdfIds: []       // 上次提问使用的PDF ID
  })

  // 计算属性
  const currentChat = computed(() => 
    chats.value.find(c => c.id === currentChatId.value)
  )

  const currentMessages = computed(() => 
    currentChat.value?.messages || []
  )

  function toTimestamp(value) {
    if (!value) return 0
    if (value instanceof Date) return value.getTime()
    const parsed = Date.parse(String(value))
    return Number.isFinite(parsed) ? parsed : 0
  }

  function sortChatsInPlace() {
    chats.value.sort((a, b) => {
      const aPinned = Boolean(a?.isPinned)
      const bPinned = Boolean(b?.isPinned)
      if (aPinned !== bPinned) {
        return aPinned ? -1 : 1
      }

      const updatedDiff = toTimestamp(b?.updatedAt) - toTimestamp(a?.updatedAt)
      if (updatedDiff !== 0) {
        return updatedDiff
      }

      const createdDiff = toTimestamp(b?.createdAt) - toTimestamp(a?.createdAt)
      if (createdDiff !== 0) {
        return createdDiff
      }

      return String(a?.id || '').localeCompare(String(b?.id || ''))
    })
  }

  function touchChat(chat, when = new Date()) {
    if (!chat) return
    const nextTime = when instanceof Date ? when.toISOString() : String(when || new Date().toISOString())
    chat.updatedAt = nextTime
    sortChatsInPlace()
  }

  function normalizeStepStatus(status, fallback = 'processing') {
    const raw = String(status || '').trim().toLowerCase()
    if (['processing', 'in_progress', 'running', 'pending'].includes(raw)) return 'processing'
    if (['success', 'succeeded', 'completed', 'complete', 'done', 'ok'].includes(raw)) return 'success'
    if (['error', 'failed', 'fail', 'failure'].includes(raw)) return 'error'
    return fallback
  }

  function normalizeStepTitle(step = {}) {
    const title = String(step?.title || '').trim()
    if (title) return title
    const message = String(step?.message || step?.content || step?.step || '').trim()
    if (!message) return '处理中'
    const short = message.replace(/\s+/g, ' ')
    return short.length > 18 ? short.slice(0, 18) : short
  }

  function normalizeSteps(steps) {
    if (!Array.isArray(steps)) return []
    return steps
      .map((step, idx) => {
        if (!step || typeof step !== 'object') return null
        const stepKey = String(step?.step || `step_${idx + 1}`).trim() || `step_${idx + 1}`
        const message = String(step?.message || step?.content || stepKey).trim() || stepKey
        return {
          ...step,
          step: stepKey,
          message,
          title: normalizeStepTitle(step),
          detail: String(step?.detail || '').trim(),
          status: normalizeStepStatus(step?.status),
          updatedAt: String(step?.updatedAt || step?.updated_at || ''),
        }
      })
      .filter(Boolean)
  }

  function normalizeReferences(references) {
    if (!Array.isArray(references)) return []
    return references
      .map((item) => {
        if (typeof item === 'string') {
          const doi = item.trim()
          return doi ? { doi, title: '' } : null
        }
        if (!item || typeof item !== 'object') return null
        const doi = String(item?.doi || '').trim()
        if (!doi) return null
        return {
          ...item,
          doi,
          title: String(item?.title || '').trim(),
        }
      })
      .filter(Boolean)
  }

  function normalizeReferenceLinks(referenceLinks) {
    if (!Array.isArray(referenceLinks)) return []
    return referenceLinks
      .map((item) => {
        if (typeof item === 'string') {
          const value = item.trim()
          return value || null
        }
        if (!item || typeof item !== 'object') return null
        const doi = String(item?.doi || '').trim()
        const pdfUrl = String(item?.pdfUrl || item?.pdf_url || '').trim()
        return {
          ...item,
          ...(doi ? { doi } : {}),
          ...(pdfUrl ? { pdfUrl } : {}),
        }
      })
      .filter(Boolean)
  }

  function normalizeDoiLocations(doiLocations) {
    if (!doiLocations || typeof doiLocations !== 'object') return {}
    return Object.entries(doiLocations).reduce((acc, [doi, locations]) => {
      const cleanDoi = String(doi || '').trim()
      if (!cleanDoi) return acc
      acc[cleanDoi] = Array.isArray(locations)
        ? locations.map((item) => (item && typeof item === 'object' ? { ...item } : item)).filter(Boolean)
        : []
      return acc
    }, {})
  }

  function normalizeComparableRole(role) {
    const raw = String(role || 'assistant').trim().toLowerCase()
    if (raw === 'bot') return 'assistant'
    return raw || 'assistant'
  }

  function normalizeChatSyncStatus(status, fallback = 'local') {
    const raw = String(status || '').trim().toLowerCase()
    if (['local', 'syncing', 'synced', 'failed'].includes(raw)) return raw
    return fallback
  }

  function normalizeMessage(message = {}) {
    const metadata = message?.metadata && typeof message.metadata === 'object' ? { ...message.metadata } : {}
    const rawMode = String(
      message?.queryMode || message?.query_mode || metadata?.queryMode || metadata?.query_mode || ''
    ).trim()
    const references = normalizeReferences(
      message?.references ?? metadata?.references
    )
    const referenceLinks = normalizeReferenceLinks(
      message?.referenceLinks
      ?? message?.reference_links
      ?? message?.pdf_links
      ?? metadata?.referenceLinks
      ?? metadata?.reference_links
      ?? metadata?.pdfLinks
      ?? metadata?.pdf_links
    )
    const steps = normalizeSteps(message?.steps ?? metadata?.steps)
    const doiLocations = normalizeDoiLocations(
      message?.doiLocations
      ?? message?.doi_locations
      ?? metadata?.doiLocations
      ?? metadata?.doi_locations
    )

    if (references.length > 0) {
      metadata.references = references
    }
    if (referenceLinks.length > 0) {
      metadata.reference_links = referenceLinks
      metadata.pdf_links = referenceLinks
    }
    if (steps.length > 0) {
      metadata.steps = steps
    }
    if (rawMode) {
      metadata.queryMode = rawMode
      metadata.query_mode = rawMode
    }
    if (Object.keys(doiLocations).length > 0) {
      metadata.doi_locations = doiLocations
    }

    return {
      ...message,
      role: normalizeComparableRole(message?.role || 'assistant'),
      content: String(message?.content || ''),
      timestamp: message?.timestamp || message?.created_at || new Date().toISOString(),
      metadata,
      queryMode: rawMode,
      references,
      referenceLinks,
      doiLocations,
      steps,
      stepsCollapsed: Boolean(message?.stepsCollapsed),
      isComplete: message?.isComplete !== false,
    }
  }

  function normalizeMessages(messages) {
    if (!Array.isArray(messages)) return []
    return messages.map((message) => normalizeMessage(message))
  }

  function normalizeChat(item = {}, fallback = {}) {
    const itemMessages = Array.isArray(item?.messages) ? item.messages : null
    const fallbackMessages = Array.isArray(fallback?.messages) ? fallback.messages : []
    const synced = Boolean(item?.synced ?? fallback?.synced)
    return {
      ...item,
      messages: normalizeMessages(
        itemMessages && itemMessages.length > 0 ? itemMessages : fallbackMessages
      ),
      pdf_list: Array.isArray(item?.pdf_list) ? item.pdf_list : (Array.isArray(fallback?.pdf_list) ? fallback.pdf_list : []),
      excel_list: Array.isArray(item?.excel_list) ? item.excel_list : (Array.isArray(fallback?.excel_list) ? fallback.excel_list : []),
      uploaded_files: Array.isArray(item?.uploaded_files) ? item.uploaded_files : (Array.isArray(fallback?.uploaded_files) ? fallback.uploaded_files : []),
      createdAt: item?.createdAt || fallback?.createdAt || new Date().toISOString(),
      updatedAt: item?.updatedAt || fallback?.updatedAt || item?.createdAt || fallback?.createdAt || new Date().toISOString(),
      isPinned: Boolean(item?.isPinned ?? fallback?.isPinned),
      synced,
      syncStatus: normalizeChatSyncStatus(
        item?.syncStatus ?? fallback?.syncStatus,
        synced ? 'synced' : 'local'
      ),
    }
  }

  function isTempChatId(chatId) {
    return String(chatId || '').startsWith('temp_')
  }

  function hasMeaningfulLocalChatState(chat = {}) {
    const messages = Array.isArray(chat?.messages) ? chat.messages : []
    const pdfList = Array.isArray(chat?.pdf_list) ? chat.pdf_list : []
    const excelList = Array.isArray(chat?.excel_list) ? chat.excel_list : []
    const uploadedFiles = Array.isArray(chat?.uploaded_files) ? chat.uploaded_files : []

    if (messages.some((message) => String(message?.content || '').trim())) return true
    if (pdfList.length > 0 || excelList.length > 0 || uploadedFiles.length > 0) return true

    const title = String(chat?.title || '').trim()
    return title && !isPlaceholderTitle(title)
  }

  function normalizeTitleForComparison(title) {
    const compact = String(title || '').replace(/\s+/g, ' ').trim()
    if (isPlaceholderTitle(compact)) return ''
    return compact.toLowerCase()
  }

  function getChatMessageCount(chat = {}) {
    const explicitCount = Number(chat?.messageCount || 0)
    if (Number.isFinite(explicitCount) && explicitCount > 0) return explicitCount
    return Array.isArray(chat?.messages) ? chat.messages.length : 0
  }

  function getComparableRoleMessageCount(chat = {}, role) {
    return (Array.isArray(chat?.messages) ? chat.messages : []).reduce((count, message) => {
      return normalizeComparableRole(message?.role || '') === role ? count + 1 : count
    }, 0)
  }

  function buildFileSignature(items, titleKeys = []) {
    if (!Array.isArray(items) || items.length === 0) return ''
    return items
      .map((item) => {
        const numericId = Number(item?.file_id || item?.id || 0)
        const title = titleKeys
          .map((key) => String(item?.[key] || '').replace(/\s+/g, ' ').trim().toLowerCase())
          .find(Boolean)
        if (numericId > 0) return `id:${numericId}`
        if (title) return `title:${title}`
        return ''
      })
      .filter(Boolean)
      .sort()
      .join('|')
  }

  function buildComparableChatSignature(chat = {}) {
    if (!hasMeaningfulLocalChatState(chat)) return ''
    return JSON.stringify({
      title: normalizeTitleForComparison(chat?.title),
      messageCount: getChatMessageCount(chat),
      userCount: getComparableRoleMessageCount(chat, 'user'),
      assistantCount: getComparableRoleMessageCount(chat, 'assistant'),
      pdfs: buildFileSignature(chat?.pdf_list, ['pdf_title', 'title', 'name']),
      excels: buildFileSignature(chat?.excel_list, ['excel_title', 'title', 'name']),
      uploads: buildFileSignature(chat?.uploaded_files, ['name', 'title', 'file_name']),
    })
  }

  function areChatsLikelySameConversation(left, right) {
    const leftSignature = buildComparableChatSignature(left)
    const rightSignature = buildComparableChatSignature(right)
    if (!leftSignature || !rightSignature || leftSignature !== rightSignature) {
      return false
    }

    const leftCreatedAt = toTimestamp(left?.createdAt || left?.updatedAt)
    const rightCreatedAt = toTimestamp(right?.createdAt || right?.updatedAt)
    if (!leftCreatedAt || !rightCreatedAt) return true

    return Math.abs(leftCreatedAt - rightCreatedAt) <= 5 * 60 * 1000
  }

  function pickPreferredChatCandidate(current, candidate) {
    if (!current) return candidate

    const currentSynced = Boolean(current?.synced)
    const candidateSynced = Boolean(candidate?.synced)
    if (currentSynced !== candidateSynced) {
      return candidateSynced ? candidate : current
    }

    const currentMessages = Array.isArray(current?.messages) ? current.messages.length : 0
    const candidateMessages = Array.isArray(candidate?.messages) ? candidate.messages.length : 0
    if (currentMessages !== candidateMessages) {
      return candidateMessages > currentMessages ? candidate : current
    }

    const currentFiles = (Array.isArray(current?.pdf_list) ? current.pdf_list.length : 0)
      + (Array.isArray(current?.excel_list) ? current.excel_list.length : 0)
      + (Array.isArray(current?.uploaded_files) ? current.uploaded_files.length : 0)
    const candidateFiles = (Array.isArray(candidate?.pdf_list) ? candidate.pdf_list.length : 0)
      + (Array.isArray(candidate?.excel_list) ? candidate.excel_list.length : 0)
      + (Array.isArray(candidate?.uploaded_files) ? candidate.uploaded_files.length : 0)
    if (currentFiles !== candidateFiles) {
      return candidateFiles > currentFiles ? candidate : current
    }

    return toTimestamp(candidate?.updatedAt || candidate?.createdAt) >= toTimestamp(current?.updatedAt || current?.createdAt)
      ? candidate
      : current
  }

  function sanitizeChats(rawChats = [], options = {}) {
    const preferredChatId = String(options?.preferredChatId || '').trim()
    const preservePreferredTemp = Boolean(options?.preservePreferredTemp)
    const deduped = new Map()

    ;(Array.isArray(rawChats) ? rawChats : []).forEach((chat) => {
      const normalized = normalizeChat(chat)
      const chatId = String(normalized?.id || '').trim()
      if (!chatId) return
      const existing = deduped.get(chatId)
      deduped.set(chatId, pickPreferredChatCandidate(existing, normalized))
    })

    const normalizedChats = Array.from(deduped.values())
    const syncedChats = normalizedChats.filter((chat) => Boolean(chat?.synced))

    const sanitized = normalizedChats.filter((chat) => {
      if (Boolean(chat?.synced)) return true
      if (!isTempChatId(chat?.id)) return true
      const isPreferredTemp = String(chat?.id || '') === preferredChatId
      if (!hasMeaningfulLocalChatState(chat)) {
        return preservePreferredTemp && isPreferredTemp
      }
      return !syncedChats.some((syncedChat) => areChatsLikelySameConversation(chat, syncedChat))
    })

    sanitized.forEach((chat) => {
      reindexConversationFiles(chat)
    })

    return sanitized
  }

  function compareConversationFiles(a = {}, b = {}) {
    const aNo = Number(a?.file_no || 0)
    const bNo = Number(b?.file_no || 0)
    if (aNo > 0 && bNo > 0 && aNo !== bNo) {
      return aNo - bNo
    }
    if (aNo > 0 && bNo <= 0) return -1
    if (aNo <= 0 && bNo > 0) return 1

    const aTime = toTimestamp(a?.uploaded_at || a?.status_updated_at || a?.created_at)
    const bTime = toTimestamp(b?.uploaded_at || b?.status_updated_at || b?.created_at)
    if (aTime !== bTime) {
      return aTime - bTime
    }

    return Number(a?.file_id || a?.id || 0) - Number(b?.file_id || b?.id || 0)
  }

  function reindexConversationFiles(chat) {
    if (!chat) return

    const merged = []
    ;(Array.isArray(chat.pdf_list) ? chat.pdf_list : []).forEach((item) => {
      merged.push({ kind: 'pdf', item })
    })
    ;(Array.isArray(chat.excel_list) ? chat.excel_list : []).forEach((item) => {
      merged.push({ kind: 'excel', item })
    })

    merged.sort((left, right) => compareConversationFiles(left.item, right.item))

    merged.forEach(({ item }, index) => {
      item.display_no = index + 1
    })
  }

  // ==================== 用户管理 ====================
  
  function setUserId(id) {
    userId.value = id
    localStorage.setItem('lfp_user_id', id)
  }

  function getUserId() {
    if (!userId.value) {
      const saved = localStorage.getItem('lfp_user_id')
      if (saved) {
        userId.value = parseInt(saved)
      }
    }
    return userId.value
  }

  function buildAutoTitleFromText(content) {
    const text = String(content || '').trim()
    if (!text) return DEFAULT_CHAT_TITLE
    return text.substring(0, 30) + (text.length > 30 ? '...' : '')
  }

  function buildAutoTitleFromFileName(fileName) {
    const rawName = String(fileName || '').trim()
    if (!rawName) return DEFAULT_CHAT_TITLE
    const withoutExt = rawName.replace(/\.[^.]+$/, '').trim()
    const normalized = withoutExt.replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim()
    return buildAutoTitleFromText(normalized || rawName)
  }

  function isPlaceholderTitle(title) {
    const text = String(title || '').trim()
    return !text || text === DEFAULT_CHAT_TITLE || text === '新会话' || text === 'New Conversation'
  }

  function countMessagesByRole(messages, role) {
    return (Array.isArray(messages) ? messages : []).filter(item => item?.role === role).length
  }

  async function updateCurrentChatTitle(title, options = {}) {
    const { persist = true, onlyIfPlaceholder = false } = options
    if (!currentChat.value) return false

    const nextTitle = buildAutoTitleFromText(title)
    if (!nextTitle) return false
    if (onlyIfPlaceholder && !isPlaceholderTitle(currentChat.value.title)) return false
    if (currentChat.value.title === nextTitle) return true

    currentChat.value.title = nextTitle
    saveChats()

    if (persist && currentChat.value.synced) {
      const uid = getUserId()
      if (uid) {
        try {
          const response = await api.updateConversationTitle(parseInt(currentChat.value.id), uid, nextTitle)
          if (response?.title) {
            currentChat.value.title = response.title
            saveChats()
          }
        } catch (e) {
          console.error('同步对话标题失败:', e)
        }
      }
    }
    return true
  }

  // ==================== 对话加载（服务器优先）====================
  
  async function loadChats() {
    const uid = getUserId()
    const saved = localStorage.getItem(CHATS_STORAGE_KEY)
    let cachedChats = []
    if (saved) {
      try {
        cachedChats = restorePersistedChats(JSON.parse(saved) || [])
      } catch (e) {
        cachedChats = []
      }
    }
    cachedChats = sanitizeChats(cachedChats, {
      preferredChatId: String(currentChatId.value || localStorage.getItem(ACTIVE_CHAT_STORAGE_KEY) || '').trim(),
    })
    const cachedById = new Map(
      (Array.isArray(cachedChats) ? cachedChats : []).map((chat) => [String(chat?.id || ''), chat])
    )
    const preferredChatId = String(currentChatId.value || localStorage.getItem(ACTIVE_CHAT_STORAGE_KEY) || '').trim()
    
    if (uid) {
      // 尝试从服务器加载
      try {
        syncStatus.value = 'syncing'
        const response = await api.getConversationList(uid)
        
        if (response.conversations) {
          const mergedChats = response.conversations.map(conv => {
            const cached = cachedById.get(conv.conversation_id.toString()) || {}
            const normalized = normalizeChat({
              id: conv.conversation_id.toString(),
              title: conv.title,
              pdf_list: conv.pdf_list || [],
              createdAt: conv.created_at,
              updatedAt: conv.updated_at,
              messageCount: conv.message_count,
              synced: true,
              syncStatus: 'synced',
              isPinned: cached?.isPinned,
            }, cached)
            reindexConversationFiles(normalized)
            return normalized
          })
          const seenIds = new Set(mergedChats.map((chat) => String(chat?.id || '')))
          const localDraftChats = cachedChats.filter((chat) => !chat?.synced || String(chat?.id || '').startsWith('temp_'))
          localDraftChats.forEach((chat) => {
            const chatId = String(chat?.id || '')
            if (!chatId || seenIds.has(chatId)) return
            const normalized = normalizeChat(chat)
            reindexConversationFiles(normalized)
            mergedChats.push(normalized)
          })

          // 转换服务器数据格式为前端格式
          chats.value = sanitizeChats(mergedChats, { preferredChatId })
          sortChatsInPlace()
          currentChatId.value = chats.value.some((chat) => chat.id === preferredChatId)
            ? preferredChatId
            : (chats.value[0]?.id || null)
          
          // 同步到 localStorage
          saveChats()
          syncStatus.value = 'synced'
          return
        }
      } catch (e) {
        console.error('从服务器加载对话失败:', e)
        syncStatus.value = 'failed'
      }
    }
    
    // 降级到 localStorage
    if (saved) {
      try {
        chats.value = sanitizeChats(cachedChats, { preferredChatId })
        sortChatsInPlace()
        currentChatId.value = chats.value.some((chat) => chat.id === preferredChatId)
          ? preferredChatId
          : (chats.value[0]?.id || null)
      } catch (e) {
        chats.value = []
      }
    }
  }

  // ==================== 对话管理 ====================
  
  function persistChatsNow() {
    chats.value = sanitizeChats(chats.value, {
      preferredChatId: currentChatId.value,
      preservePreferredTemp: true,
    })
    const persistedChats = prepareChatsForPersistence(sanitizeChats(chats.value, { preferredChatId: currentChatId.value }))
    localStorage.setItem(CHATS_STORAGE_KEY, JSON.stringify(persistedChats))

    const persistedCurrentChatId = persistedChats.some((chat) => chat.id === currentChatId.value)
      ? String(currentChatId.value)
      : String(persistedChats[0]?.id || '')

    if (persistedCurrentChatId) {
      localStorage.setItem(ACTIVE_CHAT_STORAGE_KEY, persistedCurrentChatId)
    } else {
      localStorage.removeItem(ACTIVE_CHAT_STORAGE_KEY)
    }
  }

  function saveChats(options = {}) {
    const persistPolicy = resolveChatPersistPolicy({
      force: options.force,
      isStreaming: isStreaming.value,
    })

    if (persistPolicy.mode === 'immediate') {
      if (persistTimer) {
        clearTimeout(persistTimer)
        persistTimer = null
      }
      persistChatsNow()
      return
    }

    if (persistTimer) return
    persistTimer = setTimeout(() => {
      persistTimer = null
      persistChatsNow()
    }, persistPolicy.debounceMs)
  }

  function createChat() {
    const now = new Date().toISOString()
    const chat = normalizeChat({
      id: `temp_${Date.now()}`,
      title: DEFAULT_CHAT_TITLE,
      messages: [],
      createdAt: now,
      updatedAt: now,
      synced: false,  // 标记为未同步
      syncStatus: 'local',
      pdf_list: [],   // 初始化PDF列表
      isPinned: false,
    })
    chats.value.push(chat)
    sortChatsInPlace()
    currentChatId.value = chat.id
    
    // 初始化会话状态
    sessionState.value = {
      initialPdfIds: [],
      newlyUploadedPdfIds: [],
      lastUsedPdfIds: []
    }
    
    saveChats()
    return chat
  }

  async function switchChat(chatId) {
    const normalizedChatId = String(chatId || '').trim()
    if (!normalizedChatId) return

    const requestToken = ++switchChatRequestToken
    currentChatId.value = normalizedChatId
    const chat = chats.value.find(c => c.id === normalizedChatId)
    if (!chat) return
    reindexConversationFiles(chat)
    
    console.log('[switchChat] 切换到对话:', normalizedChatId, 'synced:', chat?.synced, 'messageCount:', chat?.messageCount)
    
    // 初始化会话状态 - 重要：清空newlyUploadedPdfIds
    const pdfList = chat?.pdf_list || []
    const existingPdfIds = pdfList.map(p => p.file_id).filter(id => id)
    sessionState.value = {
      initialPdfIds: existingPdfIds,
      newlyUploadedPdfIds: [],  // 切换对话时清空，只有本次会话上传的才算新
      lastUsedPdfIds: []
    }
    console.log('[switchChat] 初始化会话状态, initialPdfIds:', existingPdfIds)
    
    // 如果是服务器同步的对话，总是从服务器加载最新消息
    if (chat && chat.synced) {
      const uid = getUserId()
      if (uid) {
        try {
          console.log('[switchChat] 从服务器加载对话详情...')
          const response = await api.getConversationDetail(parseInt(chat.id), uid)
          console.log('[switchChat] 服务器返回:', response)
          console.log('[switchChat] 消息数量:', response.messages?.length)

          if (requestToken !== switchChatRequestToken || currentChatId.value !== normalizedChatId) {
            return
          }
          
          if (response.messages) {
            const cachedMessages = Array.isArray(chat.messages) ? [...chat.messages] : []
            const mergedMessages = response.messages.map((message, index) => {
              const cached = cachedMessages[index]
              const sameMessage = cached
                && normalizeComparableRole(cached?.role || '') === normalizeComparableRole(message?.role || '')
                && String(cached?.content || '') === String(message?.content || '')
              if (!sameMessage) {
                return normalizeMessage(message)
              }
              const mergedSameMessage = {
                ...cached,
                ...message,
                metadata: {
                  ...(cached?.metadata && typeof cached.metadata === 'object' ? cached.metadata : {}),
                  ...(message?.metadata && typeof message.metadata === 'object' ? message.metadata : {}),
                },
              }

              // Preserve locally restored incomplete/expanded UI state when the server
              // detail payload does not carry the corresponding state for the same message.
              const serverProvidedIsComplete = Object.prototype.hasOwnProperty.call(message || {}, 'isComplete')
              const serverProvidedStepsCollapsed = Object.prototype.hasOwnProperty.call(message || {}, 'stepsCollapsed')

              if (cached?.isComplete === false && !serverProvidedIsComplete) {
                mergedSameMessage.isComplete = false
              }
              if (cached?.stepsCollapsed === true && !serverProvidedStepsCollapsed) {
                mergedSameMessage.stepsCollapsed = true
              }

              return normalizeMessage(mergedSameMessage)
            })
            // 使用 Vue 3 的响应式方式更新数组
            chat.messages = mergedMessages
            chat.messageCount = Number(response.message_count || mergedMessages.length)
            chat.updatedAt = response.updated_at || chat.updatedAt
            chat.syncStatus = 'synced'
            
            // 更新PDF列表
            if (response.pdf_list) {
              chat.pdf_list = [...response.pdf_list]
              console.log('[switchChat] PDF列表已更新, 数量:', chat.pdf_list.length)
              
              // 重新初始化会话状态 - 更新initialPdfIds但不改变newlyUploadedPdfIds
              const existingPdfIds = chat.pdf_list.map(p => p.file_id).filter(id => id)
              sessionState.value.initialPdfIds = existingPdfIds
              console.log('[switchChat] 更新initialPdfIds:', existingPdfIds)
            }
            
            // 更新Excel列表
            if (response.excel_list) {
              chat.excel_list = [...response.excel_list]
              console.log('[switchChat] Excel列表已更新, 数量:', chat.excel_list.length)
            }

            reindexConversationFiles(chat)
            
            sortChatsInPlace()
            saveChats()
            console.log('[switchChat] 消息已更新到 chat.messages, 当前消息数:', chat.messages.length)
          }
        } catch (e) {
          if (requestToken !== switchChatRequestToken || currentChatId.value !== normalizedChatId) {
            return
          }
          console.error('[switchChat] 加载对话详情失败:', e)
          // 如果加载失败，尝试使用本地缓存的消息
          if (!chat.messages || chat.messages.length === 0) {
            chat.messages = []
          }
        }
      }
    }
  }

  async function deleteChat(chatId) {
    const chat = chats.value.find(c => c.id === chatId)
    const uid = getUserId()
    
    if (chat && chat.synced && uid) {
      // 服务器删除
      try {
        await api.deleteConversation(parseInt(chat.id), uid)
      } catch (e) {
        console.error('删除对话失败:', e)
      }
    }
    
    // 本地删除
    const index = chats.value.findIndex(c => c.id === chatId)
    if (index > -1) {
      chats.value.splice(index, 1)
      if (chatId === currentChatId.value) {
        currentChatId.value = chats.value[0]?.id || null
      }
      saveChats()
    }
  }

  function clearAllChats() {
    chats.value = []
    currentChatId.value = null
    saveChats()
  }

  // ==================== 消息管理 ====================
  
  async function addUserMessage(content) {
    console.log('[addUserMessage] 开始添加用户消息')
    console.log('[addUserMessage] currentChat.value:', currentChat.value)
    console.log('[addUserMessage] currentChatId.value:', currentChatId.value)
    
    if (!currentChat.value) {
      console.error('[addUserMessage] ❌ currentChat.value 为空，无法添加消息')
      return
    }
    
    const uid = getUserId()
    console.log('[addUserMessage] userId:', uid)
    
    // 如果是第一次发送消息且对话未同步，先在服务器创建对话
    if (!currentChat.value.synced && currentChat.value.messages.length === 0 && uid) {
      console.log('[addUserMessage] 检测到首次发送消息，准备创建服务器对话')
      try {
        syncStatus.value = 'syncing'
        currentChat.value.syncStatus = 'syncing'
        const title = buildAutoTitleFromText(content)
        console.log('[addUserMessage] 调用 api.createConversation, title:', title)
        const response = await api.createConversation(uid, title)
        console.log('[addUserMessage] 服务器返回:', response)
        
        // 保存旧的本地id
        const oldId = currentChatId.value
        console.log('[addUserMessage] 旧的本地id:', oldId)
        
        // 🔧 关键修复：直接在 chats 数组中找到并更新对话对象
        const chatIndex = chats.value.findIndex(c => c.id === oldId)
        if (chatIndex !== -1) {
          const newId = response.conversation_id.toString()
          
          // 更新对话信息
          chats.value[chatIndex].id = newId
          chats.value[chatIndex].title = response.title || title
          chats.value[chatIndex].createdAt = response.created_at
          chats.value[chatIndex].updatedAt = response.updated_at
          chats.value[chatIndex].synced = true
          chats.value[chatIndex].syncStatus = 'synced'
          
          // 同步更新 currentChatId
          currentChatId.value = newId
          
          console.log('[addUserMessage] ✅ 对话ID已更新:', oldId, '->', newId)
          console.log('[addUserMessage] ✅ currentChatId已同步:', currentChatId.value)
          
          // 验证更新后的状态
          const verifyChat = chats.value.find(c => c.id === currentChatId.value)
          console.log('[addUserMessage] 验证 currentChat:', verifyChat ? '✅ 找到' : '❌ 找不到')
          if (verifyChat) {
            console.log('[addUserMessage] 验证详情 - id:', verifyChat.id, 'synced:', verifyChat.synced, 'messages:', verifyChat.messages.length)
          }
        } else {
          console.error('[addUserMessage] ❌ 在 chats 数组中找不到对话:', oldId)
        }
        
        syncStatus.value = 'synced'
      } catch (e) {
        console.error('[addUserMessage] ❌ 创建服务器对话失败:', e)
        syncStatus.value = 'failed'
        if (currentChat.value) {
          currentChat.value.syncStatus = 'failed'
        }
        // 即使创建失败，也继续添加消息到本地
      }
    }
    
    const message = {
      role: 'user',
      content,
      timestamp: new Date()
    }
    
    currentChat.value.messages.push(message)
    touchChat(currentChat.value, message.timestamp)
    
    // 自动生成标题（如果还没有自定义标题）
    const userMessageCount = countMessagesByRole(currentChat.value.messages, 'user')
    if (userMessageCount === 1 && isPlaceholderTitle(currentChat.value.title)) {
      void updateCurrentChatTitle(content, { persist: !!currentChat.value.synced })
    } else {
      saveChats()
    }
    
    // 注意：不在这里同步用户消息到服务器
    // 用户消息会在 ask_stream 接口中统一保存，避免重复
    console.log('[addUserMessage] ✅ 用户消息已添加到本地，等待 ask_stream 保存到服务器')
  }

  async function addBotMessage(message) {
    if (!currentChat.value) {
      console.error('[addBotMessage] ❌ currentChat.value 为空，无法添加Bot消息')
      console.error('[addBotMessage] chats.value:', chats.value)
      console.error('[addBotMessage] 尝试查找对话:', chats.value.find(c => c.id === currentChatId.value))
      return
    }
    
    const botMessage = {
      role: 'assistant',
      ...message,
      timestamp: new Date()
    }
    
    currentChat.value.messages.push(botMessage)
    touchChat(currentChat.value, botMessage.timestamp)
    saveChats()
    
    // 注意：不在这里同步到服务器，因为消息可能还不完整
    // 等流式响应完成后，由 ask_stream 接口自动保存完整消息
  }

  function updateLastBotMessage(updates, options = {}) {
    if (!currentChat.value || currentChat.value.messages.length === 0) {
      console.warn('[updateLastBotMessage] 无法更新：currentChat或messages为空')
      return
    }
    
    const lastIndex = currentChat.value.messages.length - 1
    const last = currentChat.value.messages[lastIndex]
    
    if (normalizeComparableRole(last.role) === 'assistant') {
      if (updates.references !== undefined) {
        last.references = Array.isArray(updates.references) ? [...updates.references] : []
      }

      if (updates.referenceLinks !== undefined) {
        last.referenceLinks = Array.isArray(updates.referenceLinks) ? [...updates.referenceLinks] : []
      }

      Object.keys(updates).forEach(key => {
        if (key !== 'references' && key !== 'referenceLinks') {
          last[key] = updates[key]
        }
      })

      if (options.bumpActivity) {
        touchChat(currentChat.value)
      }

      if (options.persist !== false) {
        saveChats()
      }
    }
  }
  
  // 新增：添加系统消息
  function addSystemMessage(content) {
    if (!currentChat.value) return
    
    const message = {
      role: 'system',
      content,
      timestamp: new Date()
    }
    
    currentChat.value.messages.push(message)
    touchChat(currentChat.value, message.timestamp)
    saveChats()
  }
  
  // 新增：同步完整的 bot 消息到服务器（在流式响应完成后调用）
  async function syncLastBotMessage() {
    if (!currentChat.value || currentChat.value.messages.length === 0) return
    const last = currentChat.value.messages[currentChat.value.messages.length - 1]
    
    if (normalizeComparableRole(last.role) === 'assistant' && last.content) {
      const uid = getUserId()
      if (uid && currentChat.value.synced) {
        try {
          await api.addMessage(parseInt(currentChat.value.id), uid, { ...last, role: 'assistant' })
        } catch (e) {
          console.error('同步AI消息失败:', e)
          currentChat.value.synced = false
          currentChat.value.syncStatus = 'failed'
        }
      }
    }
  }

  // ==================== 其他 ====================
  
  function setStreaming(value) {
    const previousIsStreaming = isStreaming.value
    isStreaming.value = value
    if (shouldForcePersistForStreamingTransition({ previousIsStreaming, nextIsStreaming: value })) {
      saveChats({ force: true })
    }
  }

  function setKbInfo(info) {
    kbInfo.value = info
  }

  async function loadKbInfo() {
    try {
      kbInfo.value.loading = true
      const info = await api.getKbInfo()
      kbInfo.value = {
        loading: false,
        size: Number(info?.chromadb_size || info?.source_stats?.chromadb || 0),
        vectorSize: Number(info?.chromadb_size || info?.source_stats?.chromadb || 0),
        graphSize: Number(info?.source_stats?.neo4j || info?.kb_size || 0),
        graphConnected: Boolean(info?.source_stats?.neo4j_connected),
      }
    } catch (e) {
      console.error('加载知识库信息失败:', e)
      kbInfo.value = { loading: false, size: 0, vectorSize: 0, graphSize: 0, graphConnected: false }
    }
  }

  // PDF会话管理辅助函数
  function addUploadedPdf(pdfInfo) {
    if (!currentChat.value) return
    
    // 添加到对话的pdf_list
    if (!currentChat.value.pdf_list) {
      currentChat.value.pdf_list = []
    }
    currentChat.value.pdf_list.push({
      ...pdfInfo,
      parse_status: pdfInfo?.parse_status || 'uploaded',
      index_status: pdfInfo?.index_status || 'pending',
      processing_stage: pdfInfo?.processing_stage || 'uploaded',
      status_updated_at: pdfInfo?.status_updated_at || new Date().toISOString(),
      last_error: pdfInfo?.last_error || '',
      file_meta: (pdfInfo?.file_meta && typeof pdfInfo.file_meta === 'object') ? pdfInfo.file_meta : {},
    })
    reindexConversationFiles(currentChat.value)
    
    // 添加到新上传列表（使用file_id）
    const fileId = pdfInfo.file_id
    if (fileId && !sessionState.value.newlyUploadedPdfIds.includes(fileId)) {
      sessionState.value.newlyUploadedPdfIds.push(fileId)
    }

    const titleCandidate =
      String(pdfInfo?.pdf_title || pdfInfo?.file_name || pdfInfo?.original_file_name || '').trim()
    if (titleCandidate && isPlaceholderTitle(currentChat.value.title)) {
      void updateCurrentChatTitle(titleCandidate, { persist: !!currentChat.value.synced, onlyIfPlaceholder: true })
    }

    touchChat(currentChat.value)
    saveChats()
  }

  async function refreshCurrentChatFiles() {
    if (!currentChat.value || !currentChat.value.synced) return null
    const uid = getUserId()
    if (!uid) return null
    try {
      const detail = await api.getConversationDetail(parseInt(currentChat.value.id), uid)
      if (Array.isArray(detail.pdf_list)) {
        currentChat.value.pdf_list = [...detail.pdf_list]
      }
      if (Array.isArray(detail.excel_list)) {
        currentChat.value.excel_list = [...detail.excel_list]
      }
      reindexConversationFiles(currentChat.value)
      if (Array.isArray(detail.uploaded_files)) {
        currentChat.value.uploaded_files = [...detail.uploaded_files]
      }
      currentChat.value.updatedAt = detail.updated_at || currentChat.value.updatedAt
      sortChatsInPlace()
      saveChats()
      return detail
    } catch (error) {
      console.error('刷新文件状态失败:', error)
      return null
    }
  }
  
  function getAllPdfIds() {
    if (!currentChat.value || !currentChat.value.pdf_list) return []
    return currentChat.value.pdf_list.map(p => p.file_id).filter(id => id)
  }

  function getAllUploadedFileIds() {
    if (!currentChat.value) return []
    const merged = []
    if (Array.isArray(currentChat.value.pdf_list)) {
      merged.push(...currentChat.value.pdf_list.map(item => item?.file_id))
    }
    if (Array.isArray(currentChat.value.excel_list)) {
      merged.push(...currentChat.value.excel_list.map(item => item?.file_id))
    }
    const unique = []
    const seen = new Set()
    merged.forEach((id) => {
      const n = Number(id || 0)
      if (!n || seen.has(n)) return
      seen.add(n)
      unique.push(n)
    })
    return unique
  }
  
  function getNewlyUploadedPdfIds() {
    // 只返回新上传的 PDF ID，不包括 Excel/CSV
    if (!currentChat.value || !currentChat.value.pdf_list) return []
    const allPdfIds = currentChat.value.pdf_list.map(p => p.file_id).filter(id => id)
    // 从 newlyUploadedPdfIds 中筛选出属于 PDF 的 ID
    return sessionState.value.newlyUploadedPdfIds.filter(id => allPdfIds.includes(id))
  }

  function getNewlyUploadedFileIds() {
    const allIds = getAllUploadedFileIds()
    return sessionState.value.newlyUploadedPdfIds.filter(id => allIds.includes(id))
  }
  
  async function removePdf(fileId) {
    if (!currentChat.value || !currentChat.value.pdf_list) return
    const targetId = Number(fileId || 0)
    if (!targetId) return false
    
    // 从pdf_list中移除
    const index = currentChat.value.pdf_list.findIndex(
      p => Number(p?.file_id || p?.id || 0) === targetId
    )
    if (index > -1) {
      const pdf = currentChat.value.pdf_list[index]
      currentChat.value.pdf_list.splice(index, 1)
      reindexConversationFiles(currentChat.value)
      
      // 从newlyUploadedPdfIds中移除（使用file_id）
      const newIndex = sessionState.value.newlyUploadedPdfIds.findIndex(
        id => Number(id || 0) === targetId
      )
      if (newIndex > -1) {
        sessionState.value.newlyUploadedPdfIds.splice(newIndex, 1)
      }
      
      saveChats()
      
      // 如果对话已同步，调用后端API删除
      if (currentChat.value.synced) {
        const uid = getUserId()
        if (uid) {
          try {
            await api.removePdfFromConversation(parseInt(currentChat.value.id), targetId)
            console.log('PDF已从后端删除:', targetId)
            
            // 重新加载对话详情以同步文件列表
            const response = await api.getConversationDetail(parseInt(currentChat.value.id), uid)
            if (response.pdf_list) {
              currentChat.value.pdf_list = response.pdf_list
              console.log('PDF列表已同步:', response.pdf_list.length)
            }
            if (response.excel_list) {
              currentChat.value.excel_list = response.excel_list
            }
            reindexConversationFiles(currentChat.value)
            saveChats()
            return true
          } catch (e) {
            console.error('后端删除PDF失败:', e)
            // 回滚本地 optimistic 更新
            currentChat.value.pdf_list.splice(index, 0, pdf)
            reindexConversationFiles(currentChat.value)
            if (!sessionState.value.newlyUploadedPdfIds.some(id => Number(id || 0) === targetId)) {
              sessionState.value.newlyUploadedPdfIds.push(targetId)
            }
            saveChats()
            throw e
          }
        }
      }
      return true
    }

    // 兜底：本地列表未命中时，仍尝试后端删除并刷新
    if (currentChat.value.synced) {
      const uid = getUserId()
      if (uid) {
        await api.removePdfFromConversation(parseInt(currentChat.value.id), targetId)
        const response = await api.getConversationDetail(parseInt(currentChat.value.id), uid)
        if (response.pdf_list) {
          currentChat.value.pdf_list = response.pdf_list
        }
        if (response.excel_list) {
          currentChat.value.excel_list = response.excel_list
        }
        reindexConversationFiles(currentChat.value)
        sessionState.value.newlyUploadedPdfIds = sessionState.value.newlyUploadedPdfIds.filter(
          id => Number(id || 0) !== targetId
        )
        saveChats()
        return true
      }
    }
    return false
  }

  // Excel上传
  async function uploadExcel(file) {
    if (!currentChat.value) return null
    
    try {
      const response = await api.uploadExcel(file, currentChat.value.id)
      
      if (response.success && response.document) {
        // 初始化excel_list
        if (!currentChat.value.excel_list) {
          currentChat.value.excel_list = []
        }
        
        // 添加到excel_list
        currentChat.value.excel_list.push({
          file_id: response.document.file_id,
          file_no: Number(response.document.file_no || 0),
          display_no: Number(response.document.display_no || 0),
          excel_title: response.document.title,
          excel_path: response.document.file_path,
          file_hash: response.document.hash,
          parse_status: response.document.parse_status || 'uploaded',
          index_status: response.document.index_status || 'pending',
          processing_stage: response.document.processing_stage || 'uploaded',
          status_updated_at: new Date().toISOString(),
          last_error: '',
          file_meta: {}
        })
        reindexConversationFiles(currentChat.value)
        
        // 添加到newlyUploadedPdfIds（统一使用file_id）
        if (!sessionState.value.newlyUploadedPdfIds.includes(response.document.file_id)) {
          sessionState.value.newlyUploadedPdfIds.push(response.document.file_id)
        }

        const titleCandidate = String(response?.document?.title || file?.name || '').trim()
        if (titleCandidate && isPlaceholderTitle(currentChat.value.title)) {
          await updateCurrentChatTitle(titleCandidate, { persist: !!currentChat.value.synced, onlyIfPlaceholder: true })
        }

        touchChat(currentChat.value)
        saveChats()
        return response.document
      }
      return null
    } catch (error) {
      console.error('Excel上传失败:', error)
      return null
    }
  }
  
  // 删除Excel
  async function removeExcel(fileId) {
    if (!currentChat.value || !currentChat.value.excel_list) return
    const targetId = Number(fileId || 0)
    if (!targetId) return false
    
    const index = currentChat.value.excel_list.findIndex(
      e => Number(e?.file_id || e?.id || 0) === targetId
    )
    if (index > -1) {
      const excel = currentChat.value.excel_list[index]
      currentChat.value.excel_list.splice(index, 1)
      reindexConversationFiles(currentChat.value)
      
      // 从newlyUploadedPdfIds中移除（使用file_id）
      const fileIdIndex = sessionState.value.newlyUploadedPdfIds.findIndex(
        id => Number(id || 0) === targetId
      )
      if (fileIdIndex > -1) {
        sessionState.value.newlyUploadedPdfIds.splice(fileIdIndex, 1)
      }
      
      saveChats()
      
      // 调用后端API删除
      if (currentChat.value.synced) {
        const uid = getUserId()
        if (uid) {
          try {
            await api.removeExcelFromConversation(parseInt(currentChat.value.id), targetId)
            console.log('Excel已从后端删除:', targetId)
            
            // 重新加载对话详情以同步文件列表
            const response = await api.getConversationDetail(parseInt(currentChat.value.id), uid)
            if (response.pdf_list) {
              currentChat.value.pdf_list = response.pdf_list
            }
            if (response.excel_list) {
              currentChat.value.excel_list = response.excel_list
              console.log('Excel列表已同步:', response.excel_list.length)
            }
            reindexConversationFiles(currentChat.value)
            saveChats()
            return true
          } catch (e) {
            console.error('后端删除Excel失败:', e)
            // 回滚本地 optimistic 更新
            currentChat.value.excel_list.splice(index, 0, excel)
            reindexConversationFiles(currentChat.value)
            if (!sessionState.value.newlyUploadedPdfIds.some(id => Number(id || 0) === targetId)) {
              sessionState.value.newlyUploadedPdfIds.push(targetId)
            }
            saveChats()
            throw e
          }
        }
      }
      return true
    }

    // 兜底：本地列表未命中时，仍尝试后端删除并刷新
    if (currentChat.value.synced) {
      const uid = getUserId()
      if (uid) {
        await api.removeExcelFromConversation(parseInt(currentChat.value.id), targetId)
        const response = await api.getConversationDetail(parseInt(currentChat.value.id), uid)
        if (response.pdf_list) {
          currentChat.value.pdf_list = response.pdf_list
        }
        if (response.excel_list) {
          currentChat.value.excel_list = response.excel_list
        }
        reindexConversationFiles(currentChat.value)
        sessionState.value.newlyUploadedPdfIds = sessionState.value.newlyUploadedPdfIds.filter(
          id => Number(id || 0) !== targetId
        )
        saveChats()
        return true
      }
    }
    return false
  }

  function togglePinned(chatId) {
    const chat = chats.value.find(c => c.id === chatId)
    if (!chat) return
    chat.isPinned = !Boolean(chat.isPinned)
    sortChatsInPlace()
    saveChats({ force: true })
  }

  function persistLocalState() {
    saveChats({ force: true })
  }

  return {
    chats,
    currentChatId,
    currentChat,
    currentMessages,
    isStreaming,
    kbInfo,
    userId,
    syncStatus,
    sessionState,
    setUserId,
    getUserId,
    loadChats,
    createChat,
    buildAutoTitleFromFileName,
    updateCurrentChatTitle,
    switchChat,
    togglePinned,
    persistLocalState,
    deleteChat,
    clearAllChats,
    addUserMessage,
    addBotMessage,
    updateLastBotMessage,
    addSystemMessage,
    setStreaming,
    setKbInfo,
    loadKbInfo,
    addUploadedPdf,
    refreshCurrentChatFiles,
    getAllPdfIds,
    getAllUploadedFileIds,
    getNewlyUploadedPdfIds,
    getNewlyUploadedFileIds,
    removePdf,
    uploadExcel,
    removeExcel
  }
})
