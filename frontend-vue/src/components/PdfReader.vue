<template>
  <div v-if="isOpen" class="pdf-reader-modal">
    <div class="pdf-reader-overlay" @click="closeReader"></div>
    <div class="pdf-reader-container">
      <!-- 头部 -->
      <div class="pdf-reader-header">
        <div>
          <h2>📄 原文阅读器（增强版）</h2>
          <p class="doi-text">文档标识: {{ currentDocumentLabel }}</p>
        </div>
        <div class="header-actions">
          <button class="pdf-action-btn" @click="toggleSidePanel" title="显示/隐藏侧边栏">
            🧩 侧边栏
          </button>
          <button class="pdf-close-btn" @click="closeReader">✕</button>
        </div>
      </div>

      <!-- PDF查看区 -->
      <div class="pdf-viewer-layout" ref="layoutRef" :class="{ resizing: isResizing }">
        <!-- 左侧PDF -->
        <div class="pdf-viewer-left">
          <div class="pdf-canvas-wrapper">
            <!-- PDF错误提示 -->
            <div v-if="pdfError" class="pdf-error-container">
              <div class="pdf-error-content">
                <QuotaLimitCard v-if="pdfError?.quotaCard" :card="pdfError.quotaCard" />
                <template v-else>
                  <div class="error-icon">⚠️</div>
                  <h3>{{ pdfError.message }}</h3>
                  <p class="error-doi">DOI: {{ pdfError.doi }}</p>
                  <div class="error-actions">
                    <a
                      :href="`https://doi.org/${pdfError.doi}`"
                      target="_blank"
                      class="online-view-btn"
                    >
                      🌐 在线查看文献
                    </a>
                    <button @click="closeReader" class="close-error-btn">关闭</button>
                  </div>
                </template>
                <div v-if="pdfError?.quotaCard" class="error-actions">
                  <a
                    :href="`https://doi.org/${pdfError.doi}`"
                    target="_blank"
                    class="online-view-btn"
                  >
                    🌐 在线查看文献
                  </a>
                  <button @click="closeReader" class="close-error-btn">关闭</button>
                </div>
              </div>
            </div>

            <!-- PDF iframe (主要方案) -->
            <template v-else>
              <iframe
                :src="pdfUrl"
                class="pdf-iframe"
                frameborder="0"
                @load="handleIframeLoad"
              ></iframe>
              <!-- 加载中 -->
              <div v-if="isPdfLoading" class="pdf-loading-overlay">
                <div class="loading-spinner"></div>
                <p>加载PDF中...</p>
              </div>
            </template>
          </div>
        </div>

        <!-- 分割线（可拖拽） -->
        <div
          v-show="showSidePanel"
          class="pdf-splitter"
          @mousedown.prevent="startResize"
          @touchstart.prevent="startResize"
        ></div>

        <!-- 右侧面板 - 总结/翻译 -->
        <div
          v-show="showSidePanel"
          class="right-panel"
          :style="{ width: sidebarWidth + 'px' }"
        >
          <div class="side-mode-switch">
            <button
              class="mode-btn"
              :class="{ active: panelMode === 'summary' }"
              @click="setPanelMode('summary')"
            >
              总结
            </button>
            <button
              class="mode-btn"
              :class="{ active: panelMode === 'translation' }"
              @click="setPanelMode('translation')"
            >
              翻译
            </button>
          </div>

          <div class="assist-panels">
            <!-- 全文总结面板 -->
            <div
              v-show="isSummaryVisible"
              class="summary-panel"
                          >
              <div class="summary-panel-header">
                <h3>🧾 全文总结</h3>
                <button
                  class="summary-generate-btn"
                  :disabled="isSummarizing || !currentDoi"
                  @click="generateSummary(true)"
                >
                  {{ isSummarizing ? '生成中...' : (summaryText ? '重新生成' : '生成总结') }}
                </button>
              </div>
              <div class="summary-panel-content">
                <QuotaLimitCard v-if="summaryQuotaCard" :card="summaryQuotaCard" />
                <p v-else-if="summaryError" class="summary-error">{{ summaryError }}</p>
                <p v-else-if="isSummarizing" class="summary-loading">正在生成全文总结，请稍候...</p>
                <p v-else-if="summaryText" class="summary-text">{{ summaryText }}</p>
                <p v-else class="summary-placeholder">点击“生成总结”可快速获取论文核心结论。</p>
              </div>
            </div>

            <!-- 翻译面板 -->
            <div v-show="isTranslationVisible" class="translation-panel">
              <div class="translation-panel-header">
                <h3>🌐 翻译助手</h3>
                <p>支持片段翻译，也支持整篇文档翻译</p>
                <div class="translation-subtab-switch">
                  <button
                    class="translation-subtab-btn"
                    :class="{ active: translationSubTab === 'snippet' }"
                    @click="setTranslationSubTab('snippet')"
                  >
                    片段翻译
                  </button>
                  <button
                    class="translation-subtab-btn"
                    :class="{ active: translationSubTab === 'document' }"
                    @click="setTranslationSubTab('document')"
                  >
                    全文翻译
                  </button>
                </div>
              </div>

              <div v-if="translationSubTab === 'snippet'" ref="translationBodyRef" class="translation-body">
                <div class="translation-panel-content">
                  <QuotaLimitCard v-if="translationQuotaCard" :card="translationQuotaCard" />
                  <p v-if="clipboardFeedback && !translationQuotaCard" class="translation-feedback">
                    {{ clipboardFeedback }}
                  </p>
                  <!-- 欢迎页 -->
                  <div v-if="!translationQuotaCard && translations.length === 0" class="translation-welcome">
                    <div class="welcome-icon">📖</div>
                    <p class="welcome-title">欢迎使用翻译助手</p>
                    <p class="welcome-desc">在下方输入框粘贴英文文本，点击翻译按钮即可</p>
                  </div>

                  <!-- 翻译历史 -->
                  <div v-for="(item, index) in translations" :key="index" class="translation-item">
                    <div class="translation-item-header">
                      <span class="translation-time">{{ item.time }}</span>
                    </div>
                    <div class="translation-item-content">
                      <div class="translation-source">
                        <div class="lang-label">🇬🇧 英文</div>
                        <div class="text-content">{{ item.source }}</div>
                      </div>
                      <div class="translation-target">
                        <div class="lang-label">🇨🇳 中文</div>
                        <div class="text-content" :class="{ loading: item.loading }">
                          {{ item.loading ? '翻译中...' : item.translation }}
                        </div>
                      </div>
                    </div>
                  </div>
                </div>

                <div
                  class="translation-splitter"
                  @mousedown.prevent="startTranslationResize"
                  @touchstart.prevent="startTranslationResize"
                ></div>

                <!-- 翻译按钮 -->
                <div class="translation-actions" :style="{ height: translationInputHeight + 'px' }">
                  <!-- 手动输入框 (备用方案) -->
                  <textarea
                    v-model="manualText"
                    class="manual-input"
                    placeholder="在此粘贴要翻译的英文文本..."
                    rows="3"
                  ></textarea>
                  <p class="translation-hint">读取系统剪贴板内容，不是当前 PDF 划选内容</p>
                  <div class="translation-button-row">
                    <button
                      class="translate-btn"
                      :disabled="!hasManualTranslateText || isTranslating"
                      @click="translateSelected"
                    >
                      {{ isTranslating ? '⏳ 翻译中...' : '🌐 翻译文本' }}
                    </button>
                    <button
                      class="translate-btn secondary"
                      :disabled="isTranslating"
                      @click="pasteAndTranslate"
                    >
                      {{ isTranslating ? '⏳ 翻译中...' : '📋 粘贴并翻译' }}
                    </button>
                  </div>
                </div>
              </div>

              <div v-else class="translation-document-panel">
                <div class="translation-document-actions">
                  <button
                    class="summary-generate-btn"
                    :disabled="isDocumentTranslating || !canTranslateCurrentDocument"
                    @click="translateFullDocument(true)"
                  >
                    {{ isDocumentTranslating ? '翻译中...' : (fullDocumentTranslation ? '重新翻译全文' : '翻译全文') }}
                  </button>
                  <p class="translation-document-hint">
                    {{ canTranslateCurrentDocument ? '按当前打开的 DOI / 专利原文生成整篇中文译文。' : '当前文档暂不支持全文翻译。' }}
                  </p>
                </div>
                <div class="translation-document-content">
                  <QuotaLimitCard v-if="fullDocumentTranslationQuotaCard" :card="fullDocumentTranslationQuotaCard" />
                  <p v-else-if="fullDocumentTranslationError" class="summary-error">{{ fullDocumentTranslationError }}</p>
                  <template v-else-if="fullDocumentTranslation || isDocumentTranslating">
                    <p v-if="isDocumentTranslating" class="summary-loading">正在提取文档正文并流式生成全文翻译，请稍候...</p>
                    <p v-if="getFullDocumentTranslationStatusLabel()" class="translation-document-status">
                      全文翻译状态：{{ getFullDocumentTranslationStatusLabel() }}
                    </p>
                    <div v-if="fullDocumentTranslation" class="translation-document-text translation-document-rendered" v-html="getFullDocumentTranslationHtml()"></div>
                    <p v-else class="summary-placeholder">正在等待首段译文...</p>
                  </template>
                  <p v-else class="summary-placeholder">点击“翻译全文”可生成当前文档的整篇中文译文。</p>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import QuotaLimitCard from './QuotaLimitCard.vue'
import { fetchPdfDocument, fetchPdfDocumentByUrl } from '../api/literature'
import { api } from '../services/api'
import { buildQuotaErrorCardModel } from '../services/quota-error-formatting.js'
import { formatAnswer } from '../utils'
import {
  buildTranslatePayload,
  classifyClipboardFailure,
  getClipboardFeedbackMessage,
  normalizeClipboardText,
} from '../utils/pdfReaderClipboardTranslate.js'
import { resolvePdfReaderInitialPanelMode, isPdfReaderPanelActive } from '../utils/pdfReaderPanelMode'
import { buildPdfReaderOpenState, releasePdfBlobUrl } from '../utils/pdfReaderOpenFlow'
import { createStreamingHtmlRenderer } from '../utils/streamingRender'

// Props & Emits
const emit = defineEmits(['close'])

// State
const isOpen = ref(false)
const currentDoi = ref('')
const currentPatentId = ref('')
const currentDocumentLabel = ref('')
const pdfUrl = ref('')
const pdfError = ref(null)
const showSidePanel = ref(true)
const manualText = ref('')
const translations = ref([])
const isTranslating = ref(false)
const locationHints = ref([])  // 用于页码定位
const targetPage = ref(1)  // 目标页码
const isPdfLoading = ref(false)
const sidebarWidth = ref(360)
const isResizing = ref(false)
const layoutRef = ref(null)
const translationBodyRef = ref(null)
const panelMode = ref('summary')
const summaryText = ref('')
const summaryError = ref('')
const summaryQuotaCard = ref(null)
const translationQuotaCard = ref(null)
const clipboardFeedback = ref('')
const isSummarizing = ref(false)
const activeBlobUrl = ref('')
const translationSessionId = ref(0)
const translationInputHeight = ref(240)
const isTranslationResizing = ref(false)
const translationSubTab = ref('snippet')
const fullDocumentTranslation = ref('')
const fullDocumentTranslationError = ref('')
const fullDocumentTranslationQuotaCard = ref(null)
const isDocumentTranslating = ref(false)
const fullDocumentTranslationCacheStatus = ref('')
const fullDocumentTranslationMessage = ref({ content: '' })
const documentTranslationSessionId = ref(0)
const documentTranslationAbortController = ref(null)
const renderFullDocumentTranslationHtml = createStreamingHtmlRenderer()

const MIN_SIDEBAR_WIDTH = 260
const MIN_LEFT_WIDTH = 420
const MIN_TRANSLATION_INPUT_HEIGHT = 140
const MIN_TRANSLATION_HISTORY_HEIGHT = 120
const isSummaryVisible = computed(() => isPdfReaderPanelActive(panelMode.value, 'summary'))
const isTranslationVisible = computed(() => isPdfReaderPanelActive(panelMode.value, 'translation'))
const hasManualTranslateText = computed(() => normalizeClipboardText(manualText.value).length > 0)
const canTranslateCurrentDocument = computed(() => Boolean(currentDoi.value || currentPatentId.value))

function withPdfPageAnchor(url, page = null) {
  if (!url || !page) return url
  return `${url}#page=${page}`
}

function buildDocAssistQuotaCard(error, featureTitle) {
  return buildQuotaErrorCardModel({
    code: error?.code || error?.payload?.code,
    message: error?.message || error?.payload?.message || '',
    data: error?.payload?.data,
    featureTitle,
  })
}

function resetTranslationInteractionSession() {
  translationSessionId.value += 1
  isTranslating.value = false
}

function isActiveTranslationSession(sessionId) {
  return sessionId === translationSessionId.value
}

function resetDocumentTranslationState() {
  documentTranslationSessionId.value += 1
  documentTranslationAbortController.value?.abort?.()
  documentTranslationAbortController.value = null
  fullDocumentTranslation.value = ''
  fullDocumentTranslationError.value = ''
  fullDocumentTranslationQuotaCard.value = null
  isDocumentTranslating.value = false
  fullDocumentTranslationCacheStatus.value = ''
  fullDocumentTranslationMessage.value.content = ''
}

function resolvePatentId(label, documentUrl) {
  const fromUrl = String(documentUrl || '').match(/\/api\/(?:v1\/)?patent\/original\/([^/?#]+)/i)
  if (fromUrl?.[1]) {
    try {
      return decodeURIComponent(fromUrl[1]).trim().toUpperCase()
    } catch {
      return String(fromUrl[1] || '').trim().toUpperCase()
    }
  }
  const fromLabel = String(label || '').trim().toUpperCase()
  return /^[A-Z]{2}[A-Z0-9]+$/.test(fromLabel) ? fromLabel : ''
}

function setTranslationSubTab(mode) {
  translationSubTab.value = mode === 'document' ? 'document' : 'snippet'
}

function resolveDocumentTranslationRequest() {
  if (currentDoi.value) {
    return {
      documentType: 'doi',
      documentId: currentDoi.value,
    }
  }
  if (currentPatentId.value) {
    return {
      documentType: 'patent',
      documentId: currentPatentId.value,
    }
  }
  return null
}

function getFullDocumentTranslationStatusLabel() {
  if (fullDocumentTranslationCacheStatus.value === 'hit') return '缓存命中'
  if (fullDocumentTranslationCacheStatus.value === 'partial') return '部分缓存命中'
  if (fullDocumentTranslationCacheStatus.value === 'miss') return '本次新翻译'
  return ''
}

function isActiveDocumentTranslationSession(sessionId) {
  return sessionId === documentTranslationSessionId.value
}

function getFullDocumentTranslationHtml() {
  if (!fullDocumentTranslation.value) return ''
  if (isDocumentTranslating.value) {
    return renderFullDocumentTranslationHtml(fullDocumentTranslationMessage.value)
  }
  return formatAnswer(fullDocumentTranslation.value)
}

// Methods
async function openReader(doi, locations = []) {
  resetTranslationInteractionSession()
  currentDoi.value = doi
  currentPatentId.value = ''
  currentDocumentLabel.value = doi
  locationHints.value = locations
  isPdfLoading.value = true
  summaryText.value = ''
  summaryError.value = ''
  summaryQuotaCard.value = null
  translationQuotaCard.value = null
  clipboardFeedback.value = ''
  isSummarizing.value = false
  panelMode.value = resolvePdfReaderInitialPanelMode(locations)
  translationSubTab.value = 'snippet'
  resetDocumentTranslationState()
  
  // 如果有位置信息，添加页码锚点
  if (locations.length > 0) {
    targetPage.value = locations[0].page || 1
  } else {
    targetPage.value = 1
  }
  
  pdfUrl.value = ''
  pdfError.value = null
  isOpen.value = true
  translations.value = []
  manualText.value = ''

  try {
    const loadResult = await fetchPdfDocument(doi)
    const nextState = buildPdfReaderOpenState({
      doi: currentDoi.value,
      loadResult,
      previousBlobUrl: activeBlobUrl.value,
      revokeObjectURL: (value) => releasePdfBlobUrl(value),
    })
    activeBlobUrl.value = nextState.activeBlobUrl || ''
    pdfError.value = nextState.pdfError
    pdfUrl.value = nextState.pdfUrl ? withPdfPageAnchor(nextState.pdfUrl, targetPage.value) : ''
    if (!nextState.pdfUrl) {
      isPdfLoading.value = false
    }
  } catch (error) {
    const nextState = buildPdfReaderOpenState({
      doi: currentDoi.value,
      loadResult: {
        ok: false,
        errorPayload: {
          message: error?.message || 'PDF加载失败',
          code: error?.code || '',
          data: error?.payload?.data,
          status: Number(error?.status || 0),
        },
      },
      previousBlobUrl: activeBlobUrl.value,
      revokeObjectURL: (value) => releasePdfBlobUrl(value),
    })
    activeBlobUrl.value = nextState.activeBlobUrl || ''
    pdfUrl.value = ''
    pdfError.value = nextState.pdfError
    isPdfLoading.value = false
  }
}

async function openUrlReader(label, documentUrl, locations = []) {
  resetTranslationInteractionSession()
  currentDoi.value = ''
  currentPatentId.value = resolvePatentId(label, documentUrl)
  currentDocumentLabel.value = String(label || '')
  locationHints.value = locations
  isPdfLoading.value = true
  summaryText.value = ''
  summaryError.value = ''
  summaryQuotaCard.value = null
  translationQuotaCard.value = null
  clipboardFeedback.value = ''
  isSummarizing.value = false
  panelMode.value = resolvePdfReaderInitialPanelMode(locations)
  translationSubTab.value = 'snippet'
  resetDocumentTranslationState()

  if (locations.length > 0) {
    targetPage.value = locations[0].page || 1
  } else {
    targetPage.value = 1
  }

  pdfUrl.value = ''
  pdfError.value = null
  isOpen.value = true
  translations.value = []
  manualText.value = ''

  try {
    const loadResult = await fetchPdfDocumentByUrl(documentUrl)
    const nextState = buildPdfReaderOpenState({
      doi: currentDocumentLabel.value,
      loadResult,
      previousBlobUrl: activeBlobUrl.value,
      revokeObjectURL: (value) => releasePdfBlobUrl(value),
    })
    activeBlobUrl.value = nextState.activeBlobUrl || ''
    pdfError.value = nextState.pdfError
    pdfUrl.value = nextState.pdfUrl ? withPdfPageAnchor(nextState.pdfUrl, targetPage.value) : ''
    if (!nextState.pdfUrl) {
      isPdfLoading.value = false
    }
  } catch (error) {
    const nextState = buildPdfReaderOpenState({
      doi: currentDocumentLabel.value,
      loadResult: {
        ok: false,
        errorPayload: {
          message: error?.message || 'PDF加载失败',
          code: error?.code || '',
          data: error?.payload?.data,
          status: Number(error?.status || 0),
        },
      },
      previousBlobUrl: activeBlobUrl.value,
      revokeObjectURL: (value) => releasePdfBlobUrl(value),
    })
    activeBlobUrl.value = nextState.activeBlobUrl || ''
    pdfUrl.value = ''
    pdfError.value = nextState.pdfError
    isPdfLoading.value = false
  }
}

function toggleSidePanel() {
  showSidePanel.value = !showSidePanel.value
}

function getClientX(e) {
  if (e.touches && e.touches.length) return e.touches[0].clientX
  return e.clientX
}

function getClientY(e) {
  if (e.touches && e.touches.length) return e.touches[0].clientY
  return e.clientY
}

function setPanelMode(mode) {
  panelMode.value = mode
}

function startResize(e) {
  if (!showSidePanel.value) return
  isResizing.value = true
  window.addEventListener('mousemove', handleResize)
  window.addEventListener('mouseup', stopResize)
  window.addEventListener('touchmove', handleResize, { passive: false })
  window.addEventListener('touchend', stopResize)
  handleResize(e)
}

function handleResize(e) {
  if (!isResizing.value) return
  e.preventDefault?.()
  const layoutWidth = layoutRef.value?.clientWidth || 0
  if (layoutWidth <= 0) return
  const clientX = getClientX(e)
  const nextWidth = layoutWidth - clientX
  const maxSidebarWidth = Math.max(MIN_SIDEBAR_WIDTH, layoutWidth - MIN_LEFT_WIDTH)
  sidebarWidth.value = Math.min(Math.max(nextWidth, MIN_SIDEBAR_WIDTH), maxSidebarWidth)
}

function stopResize() {
  if (!isResizing.value) return
  isResizing.value = false
  window.removeEventListener('mousemove', handleResize)
  window.removeEventListener('mouseup', stopResize)
  window.removeEventListener('touchmove', handleResize)
  window.removeEventListener('touchend', stopResize)
}

function startTranslationResize(e) {
  if (!isTranslationVisible.value) return
  isTranslationResizing.value = true
  window.addEventListener('mousemove', handleTranslationResize)
  window.addEventListener('mouseup', stopTranslationResize)
  window.addEventListener('touchmove', handleTranslationResize, { passive: false })
  window.addEventListener('touchend', stopTranslationResize)
  handleTranslationResize(e)
}

function handleTranslationResize(e) {
  if (!isTranslationResizing.value) return
  e.preventDefault?.()
  const bodyRect = translationBodyRef.value?.getBoundingClientRect?.()
  if (!bodyRect) return

  const bodyHeight = Number(bodyRect.height || 0)
  if (bodyHeight <= 0) return

  const splitterHeight = 8
  const maxInputHeight = Math.max(
    MIN_TRANSLATION_INPUT_HEIGHT,
    bodyHeight - MIN_TRANSLATION_HISTORY_HEIGHT - splitterHeight,
  )
  const pointerOffset = getClientY(e) - bodyRect.top
  const nextHeight = bodyHeight - pointerOffset - splitterHeight / 2
  translationInputHeight.value = Math.min(
    Math.max(nextHeight, MIN_TRANSLATION_INPUT_HEIGHT),
    maxInputHeight,
  )
}

function stopTranslationResize() {
  if (!isTranslationResizing.value) return
  isTranslationResizing.value = false
  window.removeEventListener('mousemove', handleTranslationResize)
  window.removeEventListener('mouseup', stopTranslationResize)
  window.removeEventListener('touchmove', handleTranslationResize)
  window.removeEventListener('touchend', stopTranslationResize)
}

function closeReader() {
  resetTranslationInteractionSession()
  resetDocumentTranslationState()
  releasePdfBlobUrl(activeBlobUrl.value)
  activeBlobUrl.value = ''
  isOpen.value = false
  currentDoi.value = ''
  currentPatentId.value = ''
  currentDocumentLabel.value = ''
  pdfUrl.value = ''
  pdfError.value = null
  stopResize()
  stopTranslationResize()
  isPdfLoading.value = false
  summaryText.value = ''
  summaryError.value = ''
  summaryQuotaCard.value = null
  translationQuotaCard.value = null
  clipboardFeedback.value = ''
  isSummarizing.value = false
  emit('close')
}

function handleIframeLoad() {
  isPdfLoading.value = false
  console.log('PDF iframe 加载完成')
}

async function generateSummary(force = false) {
  if (!currentDoi.value || isSummarizing.value) return
  if (!force && summaryText.value) return

  isSummarizing.value = true
  summaryError.value = ''
  summaryQuotaCard.value = null
  try {
    const result = await api.summarizePdf(currentDoi.value)
    const summary = String(result?.summary || result?.data?.summary || '').trim()
    if (summary) {
      summaryText.value = summary
      summaryQuotaCard.value = null
      return
    }
    summaryError.value = String(result?.error || result?.message || '总结生成失败')
  } catch (error) {
    const quotaCard = buildDocAssistQuotaCard(error, '全文总结')
    if (quotaCard) {
      summaryQuotaCard.value = quotaCard
      summaryError.value = ''
    } else {
      summaryError.value = `总结生成失败: ${error.message || '未知错误'}`
    }
  } finally {
    isSummarizing.value = false
  }
}

async function runTranslation(text, sessionId = translationSessionId.value) {
  if (!text || !isActiveTranslationSession(sessionId)) return

  const ownsBusyState = !isTranslating.value
  if (ownsBusyState) {
    isTranslating.value = true
  }

  translationQuotaCard.value = null

  const item = {
    time: new Date().toLocaleTimeString(),
    source: text,
    translation: '',
    loading: true
  }
  translations.value.unshift(item)

  try {
    const result = await api.translate(buildTranslatePayload(text))
    if (!isActiveTranslationSession(sessionId)) return

    const payload = result?.data && typeof result.data === 'object' ? result.data : result
    const translations = Array.isArray(payload?.translations) ? payload.translations : []
    if (result.success && translations.length > 0) {
      item.translation = String(translations[0] || '')
      translationQuotaCard.value = null
    } else {
      item.translation = String(result?.error || payload?.error || '翻译失败')
    }
  } catch (error) {
    if (!isActiveTranslationSession(sessionId)) return

    console.error('翻译错误:', error)
    const quotaCard = buildDocAssistQuotaCard(error, '翻译')
    if (quotaCard) {
      translationQuotaCard.value = quotaCard
      translations.value = translations.value.filter((candidate) => candidate !== item)
    } else {
      item.translation = '翻译失败: ' + (error.message || '未知错误')
    }
  } finally {
    if (!isActiveTranslationSession(sessionId)) return

    item.loading = false
    isTranslating.value = false
  }
}

async function pasteAndTranslate() {
  if (isTranslating.value) return

  const sessionId = translationSessionId.value
  isTranslating.value = true
  translationQuotaCard.value = null

  const hasNavigator = typeof navigator !== 'undefined'
  const clipboardApi = hasNavigator ? navigator.clipboard : null
  const runtimeContext = {
    hasNavigator,
    hasClipboardApi: Boolean(clipboardApi),
    hasReadText: typeof clipboardApi?.readText === 'function',
    isSecureContext: typeof window !== 'undefined' ? Boolean(window.isSecureContext) : false,
  }

  if (classifyClipboardFailure(null, runtimeContext) === 'unsupported') {
    if (!isActiveTranslationSession(sessionId)) return
    clipboardFeedback.value = getClipboardFeedbackMessage('unsupported')
    isTranslating.value = false
    return
  }

  try {
    const rawText = await clipboardApi.readText()
    if (!isActiveTranslationSession(sessionId)) return

    const text = normalizeClipboardText(rawText)
    if (!text) {
      clipboardFeedback.value = getClipboardFeedbackMessage('empty')
      isTranslating.value = false
      return
    }

    manualText.value = text
    clipboardFeedback.value = ''
    await runTranslation(text, sessionId)
  } catch (error) {
    if (!isActiveTranslationSession(sessionId)) return

    clipboardFeedback.value = getClipboardFeedbackMessage(
      classifyClipboardFailure(error, runtimeContext),
    )
    isTranslating.value = false
  }
}

async function translateSelected() {
  if (isTranslating.value) return

  const sessionId = translationSessionId.value
  const text = normalizeClipboardText(manualText.value)
  if (!text) return

  manualText.value = text
  clipboardFeedback.value = ''
  await runTranslation(text, sessionId)
}

async function translateFullDocument(force = false) {
  const request = resolveDocumentTranslationRequest()
  if (!request || isDocumentTranslating.value) return
  if (!force && fullDocumentTranslation.value) return

  documentTranslationSessionId.value += 1
  const sessionId = documentTranslationSessionId.value
  documentTranslationAbortController.value?.abort?.()
  documentTranslationAbortController.value = new AbortController()
  isDocumentTranslating.value = true
  fullDocumentTranslation.value = ''
  fullDocumentTranslationError.value = ''
  fullDocumentTranslationQuotaCard.value = null
  fullDocumentTranslationCacheStatus.value = ''
  const translatedSegments = []

  try {
    await api.translateDocumentStream(request.documentType, request.documentId, {
      signal: documentTranslationAbortController.value?.signal,
      onEvent: (event) => {
        if (!isActiveDocumentTranslationSession(sessionId)) return

        const eventType = String(event?.type || '').trim().toLowerCase()
        if (eventType === 'segment') {
          const segmentText = String(event?.translation || '').trim()
          if (segmentText) {
            translatedSegments.push(segmentText)
            fullDocumentTranslation.value = translatedSegments.join('\n\n')
          }
          if (event?.cache_status) {
            fullDocumentTranslationCacheStatus.value = String(event?.cache_status || '')
          }
          return
        }

        if (eventType === 'done') {
          const translatedText = String(event?.translated_text || '').trim()
          if (translatedText) {
            fullDocumentTranslation.value = translatedText
          }
          fullDocumentTranslationError.value = ''
          fullDocumentTranslationCacheStatus.value = String(event?.cache_status || '')
          return
        }

        if (eventType === 'error') {
          fullDocumentTranslationError.value = String(event?.message || event?.error || '全文翻译失败')
          return
        }

        if (eventType === 'start' && event?.cache_status) {
          fullDocumentTranslationCacheStatus.value = String(event?.cache_status || '')
        }
      },
    })

    if (!isActiveDocumentTranslationSession(sessionId)) return
    if (!fullDocumentTranslation.value && !fullDocumentTranslationError.value) {
      fullDocumentTranslationError.value = '全文翻译失败'
    }
  } catch (error) {
    if (!isActiveDocumentTranslationSession(sessionId)) return
    if (error?.name === 'AbortError') return
    const quotaCard = buildDocAssistQuotaCard(error, '全文翻译')
    if (quotaCard) {
      fullDocumentTranslationQuotaCard.value = quotaCard
      fullDocumentTranslationError.value = ''
    } else {
      fullDocumentTranslationError.value = `全文翻译失败: ${error.message || '未知错误'}`
    }
  } finally {
    if (isActiveDocumentTranslationSession(sessionId)) {
      documentTranslationAbortController.value = null
      isDocumentTranslating.value = false
    }
  }
}

watch(manualText, () => {
  if (clipboardFeedback.value) {
    clipboardFeedback.value = ''
  }
})

watch(fullDocumentTranslation, () => {
  fullDocumentTranslationMessage.value.content = fullDocumentTranslation.value
})

// Expose methods
defineExpose({
  openReader,
  openUrlReader,
  closeReader
})

onBeforeUnmount(() => {
  releasePdfBlobUrl(activeBlobUrl.value)
  stopResize()
  stopTranslationResize()
})
</script>

<style scoped>
.pdf-reader-modal {
  position: fixed;
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  z-index: 10000;
  display: flex;
  align-items: center;
  justify-content: center;
}

.pdf-reader-overlay {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background: rgba(0, 0, 0, 0.75);
  backdrop-filter: blur(4px);
}

.pdf-reader-container {
  position: relative;
  width: 95vw;
  height: 95vh;
  background: white;
  border-radius: 16px;
  box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  animation: slideIn 0.3s ease-out;
}

@keyframes slideIn {
  from {
    opacity: 0;
    transform: translateY(50px) scale(0.95);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

.pdf-reader-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 20px 30px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
}

.pdf-reader-header h2 {
  margin: 0;
  font-size: 20px;
}

.doi-text {
  font-size: 13px;
  opacity: 0.9;
  margin: 5px 0 0 0;
}

.header-actions {
  display: flex;
  gap: 10px;
  align-items: center;
}

.pdf-action-btn {
  padding: 8px 16px;
  background: rgba(255, 255, 255, 0.2);
  color: white;
  border: 1px solid rgba(255, 255, 255, 0.3);
  border-radius: 8px;
  cursor: pointer;
  font-size: 14px;
  transition: all 0.2s;
}

.pdf-action-btn:hover {
  background: rgba(255, 255, 255, 0.3);
  transform: translateY(-2px);
}

.pdf-close-btn {
  padding: 8px 16px;
  background: rgba(239, 68, 68, 0.9);
  color: white;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  font-size: 18px;
  font-weight: bold;
  transition: all 0.2s;
}

.pdf-close-btn:hover {
  background: rgba(220, 38, 38, 1);
}

.pdf-viewer-layout {
  display: flex;
  flex: 1;
  overflow: hidden;
  position: relative;
}

.pdf-viewer-left {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: #f3f4f6;
  min-width: 420px;
}

.right-panel {
  width: 360px;
  display: flex;
  flex-direction: column;
  background: white;
  border-left: 1px solid #e5e7eb;
  overflow: hidden;
  flex: 0 0 auto;
  min-width: 0;
}

.side-mode-switch {
  display: flex;
  gap: 8px;
  padding: 10px 12px;
  border-bottom: 1px solid #e5e7eb;
  background: #f8fafc;
}

.mode-btn {
  flex: 1;
  padding: 7px 8px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  background: white;
  color: #475569;
  font-size: 12px;
  cursor: pointer;
}

.mode-btn.active {
  border-color: #6366f1;
  color: #4338ca;
  background: #eef2ff;
}

.pdf-splitter {
  width: 6px;
  cursor: col-resize;
  background: linear-gradient(90deg, #e5e7eb, #cbd5e1, #e5e7eb);
  position: relative;
}

.pdf-splitter::after {
  content: '';
  position: absolute;
  top: 50%;
  left: 50%;
  width: 2px;
  height: 40px;
  background: #94a3b8;
  border-radius: 2px;
  transform: translate(-50%, -50%);
}

.pdf-viewer-layout.resizing .pdf-iframe {
  pointer-events: none;
}

.pdf-canvas-wrapper {
  flex: 1;
  position: relative;
  overflow: auto;
  background: #f3f4f6;
}

.pdf-iframe {
  width: 100%;
  height: 100%;
  border: none;
}

.pdf-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #6b7280;
}

.pdf-loading-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: rgba(255, 255, 255, 0.9);
  color: #6b7280;
}

.loading-spinner {
  width: 40px;
  height: 40px;
  border: 4px solid #e5e7eb;
  border-top-color: #667eea;
  border-radius: 50%;
  animation: spin 1s linear infinite;
  margin-bottom: 16px;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.summary-panel {
  display: flex;
  flex-direction: column;
  min-height: 120px;
  border-bottom: 1px solid #e5e7eb;
  background: #f9fafb;
  overflow: hidden;
}

.summary-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 14px 16px;
  border-bottom: 1px solid #e5e7eb;
}

.summary-panel-header h3 {
  margin: 0;
  font-size: 15px;
  color: #1f2937;
}

.summary-generate-btn {
  padding: 6px 10px;
  border-radius: 8px;
  border: 1px solid #cbd5e1;
  background: white;
  font-size: 12px;
  color: #334155;
  cursor: pointer;
}

.summary-generate-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.summary-panel-content {
  flex: 1 1 auto;
  padding: 12px 16px 14px;
  font-size: 13px;
  line-height: 1.6;
  color: #475569;
  overflow-y: auto;
}

.summary-placeholder,
.summary-loading {
  margin: 0;
  color: #64748b;
}

.summary-text {
  margin: 0;
  color: #334155;
  white-space: pre-wrap;
}

.summary-error {
  margin: 0;
  color: #dc2626;
}

.translation-panel {
  width: 100%;
  display: flex;
  flex-direction: column;
  background: white;
  flex: 1 1 0;
  min-height: 220px;
  overflow: hidden;
}

.assist-panels {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 260px;
  overflow: hidden;
}

.translation-panel-header {
  padding: 20px;
  border-bottom: 1px solid #e5e7eb;
  background: #f9fafb;
}

.translation-panel-header h3 {
  margin: 0 0 8px 0;
  font-size: 16px;
  color: #374151;
}

.translation-panel-header p {
  margin: 0;
  font-size: 13px;
  color: #6b7280;
}

.translation-subtab-switch {
  display: flex;
  gap: 8px;
  margin-top: 14px;
}

.translation-subtab-btn {
  flex: 1;
  padding: 7px 10px;
  border: 1px solid #cbd5e1;
  border-radius: 999px;
  background: #ffffff;
  color: #475569;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}

.translation-subtab-btn.active {
  border-color: #6366f1;
  background: #eef2ff;
  color: #4338ca;
}

.translation-body {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

.translation-panel-content {
  flex: 1 1 auto;
  overflow-y: auto;
  padding: 20px;
  min-height: 0;
}

.translation-splitter {
  flex: 0 0 auto;
  height: 8px;
  cursor: row-resize;
  background: linear-gradient(180deg, #e5e7eb, #cbd5e1, #e5e7eb);
  position: relative;
}

.translation-splitter::after {
  content: '';
  position: absolute;
  top: 50%;
  left: 50%;
  width: 48px;
  height: 2px;
  background: #94a3b8;
  border-radius: 999px;
  transform: translate(-50%, -50%);
}

.translation-welcome {
  text-align: center;
  padding: 60px 20px;
  color: #9ca3af;
}

.welcome-icon {
  font-size: 64px;
  margin-bottom: 20px;
  animation: float 3s ease-in-out infinite;
}

@keyframes float {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-10px); }
}

.welcome-title {
  font-size: 18px;
  font-weight: 600;
  color: #6b7280;
  margin: 0 0 10px 0;
}

.welcome-desc {
  font-size: 14px;
  margin: 0;
}

.translation-item {
  margin-bottom: 20px;
  padding: 15px;
  background: #f9fafb;
  border-radius: 12px;
  border: 1px solid #e5e7eb;
  animation: slideInRight 0.4s ease-out;
}

@keyframes slideInRight {
  from {
    opacity: 0;
    transform: translateX(20px);
  }
  to {
    opacity: 1;
    transform: translateX(0);
  }
}

.translation-item-header {
  font-size: 12px;
  color: #9ca3af;
  margin-bottom: 12px;
}

.translation-item-content {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.translation-source,
.translation-target {
  padding: 12px;
  background: white;
  border-radius: 8px;
}

.lang-label {
  font-size: 12px;
  font-weight: 600;
  color: #667eea;
  margin-bottom: 8px;
}

.translation-target .lang-label {
  color: #10b981;
}

/* PDF错误提示样式 */
.pdf-error-container {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f9fafb;
}

.pdf-error-content {
  text-align: center;
  padding: 40px;
  max-width: 500px;
}

.error-icon {
  font-size: 64px;
  margin-bottom: 20px;
}

.pdf-error-content h3 {
  font-size: 20px;
  color: #374151;
  margin: 0 0 12px 0;
}

.error-doi {
  font-size: 14px;
  color: #6b7280;
  margin: 0 0 24px 0;
  font-family: monospace;
  background: white;
  padding: 8px 12px;
  border-radius: 6px;
  display: inline-block;
}

.error-actions {
  display: flex;
  gap: 12px;
  justify-content: center;
}

.online-view-btn,
.close-error-btn {
  padding: 10px 20px;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s;
  border: none;
}

.online-view-btn {
  background: #667eea;
  color: white;
  text-decoration: none;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.online-view-btn:hover {
  background: #5568d3;
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(102, 126, 234, 0.3);
}

.close-error-btn {
  background: #e5e7eb;
  color: #374151;
}

.close-error-btn:hover {
  background: #d1d5db;
}

.text-content {
  font-size: 14px;
  line-height: 1.6;
  color: #374151;
  word-wrap: break-word;
}

.text-content.loading {
  color: #9ca3af;
  font-style: italic;
}

.translation-actions {
  padding: 20px;
  border-top: 1px solid #e5e7eb;
  background: #f9fafb;
  display: flex;
  flex-direction: column;
  flex: 0 0 auto;
  gap: 12px;
  min-height: 140px;
  overflow: auto;
}

.translation-document-panel {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
  flex-direction: column;
  overflow: hidden;
}

.translation-document-actions {
  padding: 16px 20px 12px;
  border-bottom: 1px solid #e5e7eb;
  background: #f9fafb;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.translation-document-hint {
  margin: 0;
  font-size: 12px;
  color: #64748b;
  line-height: 1.5;
}

.translation-document-content {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: 18px 20px 20px;
}

.translation-document-status {
  margin: 0 0 12px 0;
  color: #475569;
  font-size: 13px;
  font-weight: 600;
}

.translation-document-text {
  margin: 0;
  color: #334155;
  line-height: 1.75;
  white-space: pre-wrap;
}

.translation-document-rendered {
  white-space: normal;
}

.translation-document-rendered :deep(h1),
.translation-document-rendered :deep(h2),
.translation-document-rendered :deep(h3),
.translation-document-rendered :deep(h4) {
  margin: 18px 0 10px;
  color: #0f172a;
  line-height: 1.35;
}

.translation-document-rendered :deep(p),
.translation-document-rendered :deep(ul),
.translation-document-rendered :deep(ol),
.translation-document-rendered :deep(blockquote),
.translation-document-rendered :deep(table) {
  margin: 10px 0;
}

.translation-document-rendered :deep(ul),
.translation-document-rendered :deep(ol) {
  padding-left: 20px;
}

.translation-document-rendered :deep(li) {
  margin: 6px 0;
}

.translation-document-rendered :deep(table) {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.translation-document-rendered :deep(th),
.translation-document-rendered :deep(td) {
  border: 1px solid #cbd5e1;
  padding: 8px 10px;
  vertical-align: top;
}

.translation-document-rendered :deep(th) {
  background: #e2e8f0;
  color: #0f172a;
}

.translation-document-rendered :deep(code) {
  padding: 2px 6px;
  border-radius: 6px;
  background: #e2e8f0;
  font-size: 12px;
}

.manual-input {
  width: 100%;
  padding: 10px;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  font-size: 14px;
  line-height: 1.5;
  resize: vertical;
  font-family: inherit;
}

.manual-input:focus {
  outline: none;
  border-color: #667eea;
  box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
}

.translate-btn {
  width: 100%;
  padding: 12px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  border: none;
  border-radius: 10px;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.3s;
}

.translate-btn:hover:not(:disabled) {
  transform: translateY(-2px);
  box-shadow: 0 10px 25px rgba(102, 126, 234, 0.4);
}

.translate-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
