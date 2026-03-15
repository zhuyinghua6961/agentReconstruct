import { computed } from 'vue';
import { buildConversationFileDownloadUrl } from '../../../api/conversation';

function ensureUploadedFiles(session) {
  if (!session) {
    return [];
  }
  if (!Array.isArray(session.uploadedFiles)) {
    session.uploadedFiles = [];
  }
  return session.uploadedFiles;
}

function upsertUploadedFile(session, item) {
  const files = ensureUploadedFiles(session);
  const existingIndex = files.findIndex((it) => String(it?.id || '') === String(item?.id || ''));
  if (existingIndex >= 0) {
    files[existingIndex] = { ...files[existingIndex], ...item };
    return;
  }
  files.push(item);
}

function mapUploadResponseToFile(resp, fallbackName, fallbackType) {
  return {
    id: resp.file_id,
    file_name: resp.filename || fallbackName || 'uploaded-file',
    file_type: fallbackType,
    local_path: resp.filepath || '',
    storage_ref: resp.storage_ref || '',
    content_type: resp.content_type || '',
    size_bytes: resp.size_bytes || null,
  };
}

export function useConversationFileActions({
  activeSession,
  ensureServerConversation,
  saveSessionState,
  onUploadPdfWorkspace,
  onUploadExcelWorkspace,
}) {
  const activeFiles = computed(() => activeSession.value?.uploadedFiles || []);

  function getActiveConversationId() {
    return activeSession.value?.serverId || null;
  }

  async function ensureConversationId() {
    let conversationId = getActiveConversationId();
    if (!conversationId && activeSession.value) {
      conversationId = await ensureServerConversation(activeSession.value);
    }
    return conversationId;
  }

  async function handleUploadPdf(file) {
    const conversationId = await ensureConversationId();
    const resp = await onUploadPdfWorkspace(file, conversationId);
    if (resp && activeSession.value && resp.file_id) {
      upsertUploadedFile(
        activeSession.value,
        mapUploadResponseToFile(resp, file?.name || '', 'pdf')
      );
      saveSessionState();
    }
    return resp;
  }

  async function handleUploadExcel(file) {
    const conversationId = await ensureConversationId();
    const resp = await onUploadExcelWorkspace(file, conversationId);
    if (resp && activeSession.value && resp.file_id) {
      upsertUploadedFile(
        activeSession.value,
        mapUploadResponseToFile(resp, file?.name || '', 'excel')
      );
      saveSessionState();
    }
    return resp;
  }

  function handleDownloadFile(file) {
    const conversationId = getActiveConversationId();
    if (!conversationId || !file?.id) {
      return;
    }
    const url = buildConversationFileDownloadUrl(conversationId, file.id);
    window.open(url, '_blank', 'noopener,noreferrer');
  }

  return {
    activeFiles,
    handleUploadPdf,
    handleUploadExcel,
    handleDownloadFile,
  };
}
