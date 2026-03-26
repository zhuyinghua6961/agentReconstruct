<template>
  <div v-if="isOpen" class="pdf-reader-modal">
    <div class="pdf-reader-overlay" @click="closeReader"></div>
    <div class="pdf-reader-container">
      <!-- 头部 -->
      <div class="pdf-reader-header">
        <div>
          <h2>📄 原文阅读器（增强版）</h2>
          <p class="doi-text">DOI: {{ currentDoi }}</p>
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

        <!-- 右侧面板 - 位置提示/总结/翻译 -->
        <div v-show="showSidePanel" class="right-panel" :style="{ width: sidebarWidth + 'px' }">
          <div class="side-mode-switch">
            <button
              class="mode-btn"
              :class="{ active: panelMode === 'both' }"
              @click="setPanelMode('both')"
            >
              总结 + 翻译
            </button>
            <button
              class="mode-btn"
              :class="{ active: panelMode === 'summary' }"
              @click="setPanelMode('summary')"
            >
              仅总结
            </button>
            <button
              class="mode-btn"
              :class="{ active: panelMode === 'translation' }"
              @click="setPanelMode('translation')"
            >
              仅翻译
            </button>
          </div>

          <!-- 位置提示面板 -->
          <div v-if="locationHints.length > 0" class="location-panel">
            <div class="location-panel-header">
              <h3>📍 引用位置</h3>
              <p>共 {{ locationHints.length }} 处引用</p>
            </div>
            <div class="location-panel-content">
              <div v-for="(hint, idx) in locationHints" :key="idx" 
                   class="location-item"
                   :class="hint.confidence">
                <div class="location-header">
                  <span class="page-badge">
                    {{ hint.section || '未知章节' }}
                  </span>
                  <span class="similarity-badge" :class="hint.confidence">
                    {{ (hint.similarity * 100).toFixed(0) }}%
                  </span>
                </div>
                <div class="location-sentence">
                  "{{ hint.answer_sentence || hint.sentence }}"
                </div>
                <div class="location-source">
                  <strong>原文片段:</strong>
                  <p>{{ hint.source_text || hint.source_preview }}</p>
                </div>
                <div v-if="hint.has_number || hint.has_unit" class="location-tags">
                  <span v-if="hint.has_number" class="tag">📊 含数值</span>
                  <span v-if="hint.has_unit" class="tag">📏 含单位</span>
                </div>
              </div>
            </div>
          </div>

          <div class="assist-panels" ref="assistPanelsRef">
            <!-- 全文总结面板 -->
            <div
              v-show="isSummaryVisible"
              class="summary-panel"
              :style="panelMode === 'both' ? { height: summaryHeight + 'px' } : null"
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
                <p v-if="summaryError" class="summary-error">{{ summaryError }}</p>
                <p v-else-if="isSummarizing" class="summary-loading">正在生成全文总结，请稍候...</p>
                <p v-else-if="summaryText" class="summary-text">{{ summaryText }}</p>
                <p v-else class="summary-placeholder">点击“生成总结”可快速获取论文核心结论。</p>
              </div>
            </div>

            <div
              v-if="panelMode === 'both'"
              class="assist-splitter"
              @mousedown.prevent="startVerticalResize"
              @touchstart.prevent="startVerticalResize"
            ></div>

            <!-- 翻译面板 -->
            <div v-show="isTranslationVisible" class="translation-panel">
              <div class="translation-panel-header">
                <h3>🌐 翻译助手</h3>
                <p>粘贴英文文本后点击翻译按钮</p>
              </div>

              <div class="translation-panel-content">
                <!-- 欢迎页 -->
                <div v-if="translations.length === 0" class="translation-welcome">
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

              <!-- 翻译按钮 -->
              <div class="translation-actions">
                <!-- 手动输入框 (备用方案) -->
                <textarea
                  v-model="manualText"
                  class="manual-input"
                  placeholder="在此粘贴要翻译的英文文本..."
                  rows="3"
                ></textarea>
                <button
                  class="translate-btn"
                  :disabled="!manualText || isTranslating"
                  @click="translateSelected"
                >
                  {{ isTranslating ? '⏳ 翻译中...' : '🌐 翻译文本' }}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref } from 'vue'
import { checkPdfAvailability } from '../api/literature'
import { api } from '../services/api'

// Props & Emits
const emit = defineEmits(['close'])

// State
const isOpen = ref(false)
const currentDoi = ref('')
const pdfUrl = ref('')
const pdfError = ref(null)
const showSidePanel = ref(true)
const manualText = ref('')
const translations = ref([])
const isTranslating = ref(false)
const locationHints = ref([])  // 位置提示
const targetPage = ref(1)  // 目标页码
const isPdfLoading = ref(false)
const sidebarWidth = ref(360)
const isResizing = ref(false)
const layoutRef = ref(null)
const panelMode = ref('both')
const assistPanelsRef = ref(null)
const summaryHeight = ref(230)
const isVerticalResizing = ref(false)
const summaryText = ref('')
const summaryError = ref('')
const isSummarizing = ref(false)

const MIN_SIDEBAR_WIDTH = 260
const MIN_LEFT_WIDTH = 420
const MIN_SUMMARY_HEIGHT = 120
const MIN_TRANSLATION_HEIGHT = 180

const isSummaryVisible = computed(() => panelMode.value !== 'translation')
const isTranslationVisible = computed(() => panelMode.value !== 'summary')

function getAuthToken() {
  return localStorage.getItem('token')
    || localStorage.getItem('agentcode.auth.token.v1')
    || ''
}

function encodeDoiPath(doi) {
  return String(doi || '')
    .split('/')
    .map((item) => encodeURIComponent(item))
    .join('/')
}

function buildPdfUrl(doi, page = null) {
  const token = getAuthToken()
  const encodedDoi = encodeDoiPath(doi)
  let url = `/api/view_pdf/${encodedDoi}`
  if (token) {
    url += `?token=${encodeURIComponent(token)}`
  }
  if (page) {
    url += `#page=${page}`
  }
  return url
}

// Methods
async function openReader(doi, locations = []) {
  currentDoi.value = doi
  locationHints.value = locations
  isPdfLoading.value = true
  summaryText.value = ''
  summaryError.value = ''
  isSummarizing.value = false
  
  let url = buildPdfUrl(doi)
  
  // 如果有位置信息，添加页码锚点
  if (locations.length > 0) {
    targetPage.value = locations[0].page || 1
    url = buildPdfUrl(doi, targetPage.value)
  } else {
    targetPage.value = 1
  }
  
  pdfUrl.value = ''
  pdfError.value = null
  isOpen.value = true
  translations.value = []
  manualText.value = ''
  nextTick(() => normalizeSummaryHeight())

  try {
    const payload = await checkPdfAvailability(doi)
    if (!payload?.exists) {
      pdfError.value = {
        message: 'PDF文件不存在',
        doi: currentDoi.value
      }
      isPdfLoading.value = false
      return
    }
    pdfUrl.value = url
  } catch (_error) {
    pdfError.value = {
      message: 'PDF文件不存在',
      doi: currentDoi.value
    }
    isPdfLoading.value = false
  }
}

function toggleSidePanel() {
  showSidePanel.value = !showSidePanel.value
  if (showSidePanel.value) {
    nextTick(() => normalizeSummaryHeight())
  }
}

function getClientX(e) {
  if (e.touches && e.touches.length) return e.touches[0].clientX
  return e.clientX
}

function getClientY(e) {
  if (e.touches && e.touches.length) return e.touches[0].clientY
  return e.clientY
}

function normalizeSummaryHeight() {
  if (panelMode.value !== 'both' || !assistPanelsRef.value) return
  const rect = assistPanelsRef.value.getBoundingClientRect()
  const minHeight = MIN_SUMMARY_HEIGHT
  const maxHeight = Math.max(minHeight, rect.height - MIN_TRANSLATION_HEIGHT)
  if (summaryHeight.value < minHeight) summaryHeight.value = minHeight
  if (summaryHeight.value > maxHeight) summaryHeight.value = maxHeight
}

function setPanelMode(mode) {
  panelMode.value = mode
  if (mode === 'both') {
    nextTick(() => normalizeSummaryHeight())
  }
}

function startResize(e) {
  if (!layoutRef.value) return
  isResizing.value = true
  onResize(e)
  window.addEventListener('mousemove', onResize)
  window.addEventListener('mouseup', stopResize)
  window.addEventListener('touchmove', onResize, { passive: false })
  window.addEventListener('touchend', stopResize)
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
}

function onResize(e) {
  if (!isResizing.value || !layoutRef.value) return
  const rect = layoutRef.value.getBoundingClientRect()
  const clientX = getClientX(e)
  let newWidth = rect.right - clientX
  const maxWidth = Math.max(MIN_SIDEBAR_WIDTH, rect.width - MIN_LEFT_WIDTH)
  if (newWidth < MIN_SIDEBAR_WIDTH) newWidth = MIN_SIDEBAR_WIDTH
  if (newWidth > maxWidth) newWidth = maxWidth
  sidebarWidth.value = Math.round(newWidth)
}

function stopResize() {
  isResizing.value = false
  window.removeEventListener('mousemove', onResize)
  window.removeEventListener('mouseup', stopResize)
  window.removeEventListener('touchmove', onResize)
  window.removeEventListener('touchend', stopResize)
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
}

function startVerticalResize(e) {
  if (!assistPanelsRef.value || panelMode.value !== 'both') return
  isVerticalResizing.value = true
  onVerticalResize(e)
  window.addEventListener('mousemove', onVerticalResize)
  window.addEventListener('mouseup', stopVerticalResize)
  window.addEventListener('touchmove', onVerticalResize, { passive: false })
  window.addEventListener('touchend', stopVerticalResize)
  document.body.style.cursor = 'row-resize'
  document.body.style.userSelect = 'none'
}

function onVerticalResize(e) {
  if (!isVerticalResizing.value || !assistPanelsRef.value) return
  if (e.cancelable) e.preventDefault()
  const rect = assistPanelsRef.value.getBoundingClientRect()
  const clientY = getClientY(e)
  let newHeight = clientY - rect.top
  const maxHeight = Math.max(MIN_SUMMARY_HEIGHT, rect.height - MIN_TRANSLATION_HEIGHT)
  if (newHeight < MIN_SUMMARY_HEIGHT) newHeight = MIN_SUMMARY_HEIGHT
  if (newHeight > maxHeight) newHeight = maxHeight
  summaryHeight.value = Math.round(newHeight)
}

function stopVerticalResize() {
  isVerticalResizing.value = false
  window.removeEventListener('mousemove', onVerticalResize)
  window.removeEventListener('mouseup', stopVerticalResize)
  window.removeEventListener('touchmove', onVerticalResize)
  window.removeEventListener('touchend', stopVerticalResize)
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
}

function jumpToPage(page) {
  targetPage.value = page
  pdfUrl.value = buildPdfUrl(currentDoi.value, page)
}

function closeReader() {
  isOpen.value = false
  currentDoi.value = ''
  pdfUrl.value = ''
  pdfError.value = null
  stopVerticalResize()
  isPdfLoading.value = false
  summaryText.value = ''
  summaryError.value = ''
  isSummarizing.value = false
  emit('close')
}

function handleIframeLoad() {
  isPdfLoading.value = false
  console.log('PDF iframe 加载完成')
}

function buildQuotaErrorMessage(error, featureName) {
  const status = Number(error?.status || 0)
  const code = String(error?.code || '')
  if (status === 429 || code === 'QUOTA_EXCEEDED') {
    return `${featureName}配额不足，请在个人中心查看剩余额度`
  }
  if (
    status === 401 ||
    code === 'TOKEN_MISSING' ||
    code === 'TOKEN_INVALID' ||
    code === 'USER_NOT_FOUND'
  ) {
    return '请先登录后使用'
  }
  return ''
}

async function generateSummary(force = false) {
  if (!currentDoi.value || isSummarizing.value) return
  if (!force && summaryText.value) return

  isSummarizing.value = true
  summaryError.value = ''
  try {
    const result = await api.summarizePdf(currentDoi.value)
    const summary = String(result?.summary || result?.data?.summary || '').trim()
    if (summary) {
      summaryText.value = summary
      return
    }
    summaryError.value = String(result?.error || result?.message || '总结生成失败')
  } catch (error) {
    const quotaMessage = buildQuotaErrorMessage(error, '全文总结')
    if (quotaMessage) {
      summaryError.value = quotaMessage
    } else {
      summaryError.value = `总结生成失败: ${error.message || '未知错误'}`
    }
  } finally {
    isSummarizing.value = false
  }
}

async function translateSelected() {
  if (!manualText.value || isTranslating.value) return

  isTranslating.value = true

  // 添加翻译项
  const item = {
    time: new Date().toLocaleTimeString(),
    source: manualText.value,
    translation: '',
    loading: true
  }
  translations.value.unshift(item)

  try {
    const result = await api.translate([manualText.value])
    const payload = result?.data && typeof result.data === 'object' ? result.data : result
    const translations = Array.isArray(payload?.translations) ? payload.translations : []
    if (result.success && translations.length > 0) {
      item.translation = String(translations[0] || '')
    } else {
      item.translation = String(result?.error || payload?.error || '翻译失败')
    }
  } catch (error) {
    console.error('翻译错误:', error)
    const quotaMessage = buildQuotaErrorMessage(error, '翻译')
    if (quotaMessage) {
      item.translation = quotaMessage
    } else {
      item.translation = '翻译失败: ' + (error.message || '未知错误')
    }
  } finally {
    item.loading = false
    isTranslating.value = false
    manualText.value = '' // 清空输入框
  }
}

// Expose methods
defineExpose({
  openReader,
  closeReader
})

onBeforeUnmount(() => {
  stopResize()
  stopVerticalResize()
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

/* 位置提示面板 */
.location-panel {
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
  border-bottom: 1px solid #e5e7eb;
  min-height: 130px;
  max-height: 38%;
}

.location-panel-header {
  padding: 20px;
  border-bottom: 1px solid #e5e7eb;
  background: #f9fafb;
}

.location-panel-header h3 {
  margin: 0 0 8px 0;
  font-size: 16px;
  color: #374151;
}

.location-panel-header p {
  margin: 0;
  font-size: 13px;
  color: #6b7280;
}

.location-panel-content {
  flex: 0 1 auto;
  overflow-y: auto;
  padding: 16px;
}

.location-item {
  margin-bottom: 16px;
  padding: 16px;
  background: #f9fafb;
  border-radius: 12px;
  border: 2px solid #e5e7eb;
  transition: all 0.2s;
}

.location-item:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0,0,0,0.1);
}

.location-item.high {
  border-color: #10b981;
  background: linear-gradient(to right, #d1fae5 0%, #f9fafb 100%);
}

.location-item.medium {
  border-color: #f59e0b;
  background: linear-gradient(to right, #fef3c7 0%, #f9fafb 100%);
}

.location-item.low {
  border-color: #ef4444;
  background: linear-gradient(to right, #fee2e2 0%, #f9fafb 100%);
}

.location-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.page-badge {
  display: inline-block;
  padding: 4px 10px;
  background: #667eea;
  color: white;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 600;
}

.similarity-badge {
  display: inline-block;
  padding: 4px 10px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 600;
}

.similarity-badge.high {
  background: #d1fae5;
  color: #065f46;
}

.similarity-badge.medium {
  background: #fef3c7;
  color: #92400e;
}

.similarity-badge.low {
  background: #fee2e2;
  color: #991b1b;
}

.location-sentence {
  font-size: 14px;
  color: #1f2937;
  margin-bottom: 12px;
  padding: 8px;
  background: white;
  border-radius: 6px;
  font-style: italic;
}

.location-source {
  font-size: 13px;
  color: #6b7280;
  margin-bottom: 12px;
  padding: 8px;
  background: white;
  border-radius: 6px;
}

.location-source strong {
  display: block;
  margin-bottom: 4px;
  color: #374151;
}

.location-source p {
  margin: 0;
  line-height: 1.5;
}

.location-tags {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}

.location-tags .tag {
  display: inline-block;
  padding: 4px 8px;
  background: #e0e7ff;
  color: #4338ca;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
}

.jump-btn {
  width: 100%;
  padding: 8px;
  background: #667eea;
  color: white;
  border: none;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s;
}

.jump-btn:hover {
  background: #5568d3;
  transform: translateY(-1px);
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

.assist-splitter {
  height: 8px;
  cursor: row-resize;
  background: linear-gradient(180deg, #e5e7eb, #cbd5e1, #e5e7eb);
  border-top: 1px solid #e5e7eb;
  border-bottom: 1px solid #e5e7eb;
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

.translation-panel-content {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
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
  gap: 12px;
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
