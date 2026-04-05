function normalizeFileId(value) {
  const numeric = Number(value || 0)
  return Number.isInteger(numeric) && numeric > 0 ? numeric : 0
}

function dedupePositiveIds(values = []) {
  const unique = []
  const seen = new Set()
  values.forEach((value) => {
    const normalized = normalizeFileId(value)
    if (!normalized || seen.has(normalized)) return
    seen.add(normalized)
    unique.push(normalized)
  })
  return unique
}

export function getLatestAssistantMessage(messages = []) {
  const list = Array.isArray(messages) ? messages : []
  return [...list].reverse().find((message) => {
    const role = String(message?.role || '').trim().toLowerCase()
    return role === 'assistant' || role === 'bot'
  }) || null
}

export function getLastFocusFileIdsFromMessages(messages = []) {
  const latestBot = getLatestAssistantMessage(messages)
  const usedFiles = latestBot?.metadata?.used_files
  if (!Array.isArray(usedFiles) || usedFiles.length === 0) return []
  return dedupePositiveIds(usedFiles.map((item) => item?.file_id))
}

export function getLastTurnRouteFromMessages(messages = []) {
  const latestBot = getLatestAssistantMessage(messages)
  return String(latestBot?.metadata?.route || '').trim().toLowerCase()
}

export function getAllUploadedFileIdsFromChat(chat = {}) {
  const fileIds = []
  if (Array.isArray(chat?.pdf_list)) {
    fileIds.push(...chat.pdf_list.map((item) => item?.file_id))
  }
  if (Array.isArray(chat?.excel_list)) {
    fileIds.push(...chat.excel_list.map((item) => item?.file_id))
  }
  return dedupePositiveIds(fileIds)
}

export function getNewlyUploadedFileIdsForChat(chat = {}, sessionState = {}) {
  const availableIds = new Set(getAllUploadedFileIdsFromChat(chat))
  const candidateIds = Array.isArray(sessionState?.newlyUploadedPdfIds) ? sessionState.newlyUploadedPdfIds : []
  return dedupePositiveIds(candidateIds.filter((value) => availableIds.has(normalizeFileId(value))))
}

export function buildChatRequestContext({ chat = {}, sessionState = {}, selectedFileIds = [] } = {}) {
  return {
    newly_uploaded_ids: getNewlyUploadedFileIdsForChat(chat, sessionState),
    all_available_ids: getAllUploadedFileIdsFromChat(chat),
    selected_ids: dedupePositiveIds(selectedFileIds),
    last_focus_ids: getLastFocusFileIdsFromMessages(chat?.messages || []),
    last_turn_route: getLastTurnRouteFromMessages(chat?.messages || []),
  }
}
