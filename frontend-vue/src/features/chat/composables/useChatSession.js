import { computed, ref } from 'vue';
import {
  createConversation,
  deleteConversation,
  getConversationDetail,
  listConversations,
} from '../../../api/conversation';
import { streamAsk } from '../../../api/chat';

const STORAGE_KEY = 'agentcode.frontend.sessions.v2';
const MAX_SESSIONS = 30;

function createSession() {
  const id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  return {
    id,
    serverId: null,
    title: '新会话',
    createdAt: Date.now(),
    messages: [],
    uploadedFiles: [],
    metadata: {
      queryMode: '',
      references: [],
      pdfLinks: [],
    },
    hydratedFromServer: false,
  };
}

function normalizeMessages(messages) {
  if (!Array.isArray(messages)) {
    return [];
  }
  return messages
    .map((msg) => ({
      role: msg?.role === 'assistant' ? 'assistant' : 'user',
      content: String(msg?.content || ''),
      ts: Number(msg?.ts) || Date.now(),
    }))
    .slice(-120);
}

function normalizePdfLinks(links) {
  if (!Array.isArray(links)) {
    return [];
  }

  const result = [];
  const unique = new Set();
  for (const item of links) {
    const doi = String(item?.doi || '').trim();
    const pdfUrl = String(item?.pdf_url || item?.pdfUrl || '').trim();
    if (!doi || unique.has(doi)) {
      continue;
    }
    unique.add(doi);
    result.push({ doi, pdfUrl });
  }
  return result;
}

function normalizeReferences(references) {
  if (!Array.isArray(references)) {
    return [];
  }
  const unique = new Set();
  for (const item of references) {
    const doi = String(item || '').trim();
    if (doi) {
      unique.add(doi);
    }
  }
  return Array.from(unique).slice(0, 80);
}

function normalizeMetadata(metadata) {
  return {
    queryMode: String(metadata?.queryMode || metadata?.query_mode || ''),
    references: normalizeReferences(metadata?.references),
    pdfLinks: normalizePdfLinks(metadata?.pdfLinks || metadata?.pdf_links),
  };
}

function normalizeSession(raw) {
  return {
    id: String(raw?.id || `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`),
    serverId: Number(raw?.serverId) || null,
    title: String(raw?.title || '新会话').slice(0, 80),
    createdAt: Number(raw?.createdAt) || Date.now(),
    messages: normalizeMessages(raw?.messages),
    uploadedFiles: Array.isArray(raw?.uploadedFiles) ? raw.uploadedFiles : [],
    metadata: normalizeMetadata(raw?.metadata),
    hydratedFromServer: Boolean(raw?.hydratedFromServer),
  };
}

function loadStoredState() {
  if (typeof window === 'undefined') {
    return null;
  }

  try {
    const rawText = window.localStorage.getItem(STORAGE_KEY);
    if (!rawText) {
      return null;
    }
    const parsed = JSON.parse(rawText);
    const sessions = Array.isArray(parsed?.sessions)
      ? parsed.sessions.map(normalizeSession).slice(0, MAX_SESSIONS)
      : [];
    if (sessions.length === 0) {
      return null;
    }
    const activeSessionId = String(parsed?.activeSessionId || '');
    return { sessions, activeSessionId };
  } catch {
    return null;
  }
}

function persistState({ sessions, activeSessionId }) {
  if (typeof window === 'undefined') {
    return;
  }
  try {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        sessions: sessions.slice(0, MAX_SESSIONS),
        activeSessionId,
      })
    );
  } catch {
    // Ignore persistence failures.
  }
}

function mapServerConversation(item) {
  const serverId = Number(item?.conversation_id);
  const createdAt = new Date(item?.created_at || Date.now()).getTime();
  return {
    id: `srv-${serverId}`,
    serverId,
    title: String(item?.title || '新会话').slice(0, 80),
    createdAt: Number.isFinite(createdAt) ? createdAt : Date.now(),
    messages: [],
    uploadedFiles: [],
    metadata: { queryMode: '', references: [], pdfLinks: [] },
    hydratedFromServer: false,
  };
}

function referencesToPdfLinks(references) {
  return references.map((doi) => ({ doi, pdfUrl: '' }));
}

export function useChatSession({ isAuthenticatedRef } = {}) {
  const initial = loadStoredState();
  const sessions = ref(initial?.sessions?.length ? initial.sessions : [createSession()]);
  const activeSessionId = ref(
    initial?.activeSessionId &&
      sessions.value.some((session) => session.id === initial.activeSessionId)
      ? initial.activeSessionId
      : sessions.value[0].id
  );
  const isStreaming = ref(false);
  const abortController = ref(null);
  const syncError = ref('');

  const activeSession = computed(() => {
    return sessions.value.find((item) => item.id === activeSessionId.value) || sessions.value[0];
  });

  const isAuthenticated = computed(() => Boolean(isAuthenticatedRef?.value));

  function saveState() {
    persistState({
      sessions: sessions.value,
      activeSessionId: activeSessionId.value,
    });
  }

  async function syncFromServer() {
    if (!isAuthenticated.value) {
      return;
    }
    syncError.value = '';
    try {
      const resp = await listConversations(1, MAX_SESSIONS);
      if (!resp?.success) {
        throw new Error(resp?.error || '同步会话失败');
      }
      const serverSessions = (resp?.data?.conversations || []).map(mapServerConversation);
      sessions.value = serverSessions.length > 0 ? serverSessions : [createSession()];
      activeSessionId.value = sessions.value[0].id;
      saveState();
      await hydrateActiveSession();
    } catch (error) {
      syncError.value = String(error);
    }
  }

  async function hydrateActiveSession() {
    if (!isAuthenticated.value) {
      return;
    }
    const session = activeSession.value;
    if (!session?.serverId || session.hydratedFromServer) {
      return;
    }
    try {
      const resp = await getConversationDetail(session.serverId);
      if (!resp?.success) {
        return;
      }
      const detail = resp.data || {};
      session.messages = normalizeMessages(
        (detail.messages || []).map((msg) => ({
          role: msg.role,
          content: msg.content,
          ts: new Date(msg.created_at || Date.now()).getTime(),
        }))
      );
      session.uploadedFiles = Array.isArray(detail.uploaded_files)
        ? detail.uploaded_files
        : [];
      session.hydratedFromServer = true;
      saveState();
    } catch {
      // Ignore hydrate errors and keep local state.
    }
  }

  async function ensureServerConversation(session) {
    if (!isAuthenticated.value || !session) {
      return null;
    }
    if (session.serverId) {
      return session.serverId;
    }
    try {
      const resp = await createConversation(session.title || '新会话');
      if (!resp?.success) {
        return null;
      }
      const data = resp.data || {};
      const serverId = Number(data.conversation_id);
      if (!serverId) {
        return null;
      }
      session.serverId = serverId;
      session.id = `srv-${serverId}`;
      const createdAt = new Date(data.created_at || Date.now()).getTime();
      session.createdAt = Number.isFinite(createdAt) ? createdAt : Date.now();
      activeSessionId.value = session.id;
      saveState();
      return serverId;
    } catch {
      return null;
    }
  }

  async function setActiveSession(id) {
    activeSessionId.value = id;
    saveState();
    await hydrateActiveSession();
  }

  async function createNewSession() {
    const session = createSession();
    sessions.value.unshift(session);
    sessions.value = sessions.value.slice(0, MAX_SESSIONS);
    activeSessionId.value = session.id;
    saveState();
    await ensureServerConversation(session);
  }

  async function removeSession(id) {
    const target = sessions.value.find((item) => item.id === id);
    if (target?.serverId && isAuthenticated.value) {
      try {
        await deleteConversation(target.serverId);
      } catch {
        // Ignore remote deletion errors.
      }
    }

    if (sessions.value.length === 1) {
      sessions.value = [createSession()];
      activeSessionId.value = sessions.value[0].id;
      saveState();
      return;
    }

    sessions.value = sessions.value.filter((item) => item.id !== id);
    if (activeSessionId.value === id) {
      activeSessionId.value = sessions.value[0].id;
    }
    saveState();
  }

  function clearAllSessions() {
    sessions.value = [createSession()];
    activeSessionId.value = sessions.value[0].id;
    saveState();
  }

  function stopStreaming() {
    if (abortController.value) {
      abortController.value.abort();
      abortController.value = null;
    }
    isStreaming.value = false;
    saveState();
  }

  async function sendMessage({ question, usePdf, pdfPath, useGenerationDriven }) {
    const session = activeSession.value;
    if (!session || !question?.trim() || isStreaming.value) {
      return;
    }

    const cleanQuestion = question.trim();
    session.messages.push({ role: 'user', content: cleanQuestion, ts: Date.now() });
    if (session.title === '新会话') {
      session.title = cleanQuestion.slice(0, 24);
    }

    const assistantMessage = { role: 'assistant', content: '', ts: Date.now() };
    session.messages.push(assistantMessage);
    session.metadata = { queryMode: '', references: [], pdfLinks: [] };
    let pendingContent = '';
    let flushTimer = null;

    const flushPendingContent = () => {
      if (!pendingContent) return;
      assistantMessage.content += pendingContent;
      pendingContent = '';
    };

    const scheduleFlush = () => {
      if (flushTimer) return;
      flushTimer = setTimeout(() => {
        flushTimer = null;
        flushPendingContent();
      }, 40);
    };

    isStreaming.value = true;
    abortController.value = new AbortController();
    saveState();

    const chatHistory = session.messages
      .filter((msg) => msg.role === 'user' || msg.role === 'assistant')
      .slice(0, -1)
      .map((msg) => ({ role: msg.role, content: msg.content }));

    const conversationId = await ensureServerConversation(session);

    try {
      await streamAsk({
        question: cleanQuestion,
        chatHistory,
        usePdf,
        pdfPath,
        useGenerationDriven,
        conversationId,
        signal: abortController.value.signal,
        onEvent: (event) => {
          const type = event.type;

          if (type === 'content') {
            pendingContent += event.content || '';
            scheduleFlush();
            return;
          }

          if (type === 'thinking') {
            flushPendingContent();
            if (!assistantMessage.content) {
              assistantMessage.content = `\n> ${event.content || '处理中...'}\n\n`;
            }
            return;
          }

          if (type === 'metadata') {
            session.metadata.queryMode = event.query_mode || event.queryMode || '';
            return;
          }

          if (type === 'done') {
            flushPendingContent();
            const references = normalizeReferences(event.references || []);
            const pdfLinks = normalizePdfLinks(event.pdf_links || event.pdfLinks || []);
            session.metadata.references = references;
            session.metadata.pdfLinks = pdfLinks.length > 0 ? pdfLinks : referencesToPdfLinks(references);
            saveState();
            return;
          }

          if (type === 'error') {
            flushPendingContent();
            assistantMessage.content += `\n\n[错误] ${event.error || '未知错误'}`;
          }
        },
      });
    } catch (error) {
      flushPendingContent();
      if (error?.name !== 'AbortError') {
        assistantMessage.content += `\n\n[请求失败] ${String(error)}`;
      }
    } finally {
      if (flushTimer) {
        clearTimeout(flushTimer);
        flushTimer = null;
      }
      flushPendingContent();
      abortController.value = null;
      isStreaming.value = false;
      if (!assistantMessage.content) {
        assistantMessage.content = '[空响应] 后端未返回内容';
      }
      saveState();
    }
  }

  return {
    sessions,
    activeSession,
    activeSessionId,
    isStreaming,
    syncError,
    createNewSession,
    removeSession,
    clearAllSessions,
    setActiveSession,
    sendMessage,
    stopStreaming,
    syncFromServer,
    hydrateActiveSession,
    ensureServerConversation,
    saveState,
  };
}
