<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import PdfReader from '../components/PdfReader.vue'
import MarkdownRenderer from '../features/markdown/MarkdownRenderer.vue'
import { buildPdfViewUrl, getLiteratureContent, searchLiterature } from '../api/literature'
import { formatUserFacingError } from '../utils/userFacingErrors'

const router = useRouter()
const pdfReader = ref(null)

const query = ref('')
const queryType = ref('auto')
const matchMode = ref('semantic')
const limit = ref(20)

const loading = ref(false)
const error = ref('')
const results = ref([])
const resultMeta = ref({ query_type_detected: '', count: 0 })
const selectedDoi = ref('')
const detail = ref(null)
const detailLoading = ref(false)
const detailError = ref('')
const detailScrollRef = ref(null)

function shouldShowRelevance(meta, mode) {
  if (String(meta?.query_type_detected || '').toLowerCase() === 'doi') {
    return false
  }
  if (Boolean(meta?.rerank_applied)) {
    return true
  }
  return String(mode || '').toLowerCase() === 'semantic'
}

function formatRelevancePercent(score) {
  const value = Number(score)
  if (!Number.isFinite(value) || value <= 0) {
    return null
  }
  const normalized = value > 1 ? 1 : value
  const percent = Math.round(Math.min(1, Math.max(0, normalized)) * 100)
  return percent > 0 ? percent : null
}

function relevanceLabel(item) {
  const percent = formatRelevancePercent(item?.match_score)
  return percent === null ? '' : `相关度 ${percent}%`
}

function queryTypeDetectedLabel(value) {
  const normalized = String(value || '').toLowerCase()
  if (normalized === 'doi') {
    return 'DOI 精确'
  }
  if (normalized === 'title') {
    return '主题全文'
  }
  return value
}

async function runSearch() {
  const cleanQuery = String(query.value || '').trim()
  if (!cleanQuery) {
    error.value = '请输入 DOI 或检索主题'
    return
  }

  loading.value = true
  error.value = ''
  results.value = []
  selectedDoi.value = ''
  detail.value = null
  detailError.value = ''

  try {
    const payload = await searchLiterature({
      query: cleanQuery,
      queryType: queryType.value,
      matchMode: matchMode.value,
      limit: limit.value,
    })
    results.value = Array.isArray(payload?.items) ? payload.items : []
    resultMeta.value = {
      query_type_detected: payload?.query_type_detected || '',
      count: Number(payload?.count || results.value.length || 0),
      code: payload?.code || '',
      message: payload?.error || '',
      rerank_applied: Boolean(payload?.rerank?.applied),
    }
    if (payload?.error && results.value.length === 0) {
      error.value = formatUserFacingError({
        code: payload.code,
        message: payload.error,
        error: payload.error,
      })
    }
  } catch (err) {
    error.value = formatUserFacingError({ message: err?.message }) || '检索失败'
  } finally {
    loading.value = false
  }
}

async function selectResult(item) {
  const doi = String(item?.doi || '').trim()
  if (!doi) {
    return
  }
  selectedDoi.value = doi
  detailLoading.value = true
  detailError.value = ''
  detail.value = null
  if (detailScrollRef.value) {
    detailScrollRef.value.scrollTop = 0
  }
  try {
    const payload = await getLiteratureContent(doi)
    if (payload?.error) {
      detailError.value = formatUserFacingError({
        code: payload.code,
        message: payload.error,
        error: payload.error,
      })
      detail.value = {
        doi,
        title: item?.title || doi,
        authors: item?.authors || '-',
        journal: item?.journal || '-',
        publication_date: item?.publication_date || '-',
        abstract: item?.abstract || '无摘要信息',
        content: '',
      }
      return
    }
    detail.value = payload
  } catch (err) {
    detailError.value = formatUserFacingError({ message: err?.message }) || '加载文献详情失败'
  } finally {
    detailLoading.value = false
  }
}

function openPdf(doi) {
  const url = buildPdfViewUrl(doi)
  if (pdfReader.value?.open) {
    pdfReader.value.open({ doi, url })
    return
  }
  window.open(url, '_blank', 'noopener,noreferrer')
}

function goBack() {
  router.push('/')
}
</script>

<template>
  <div class="literature-search-page">
    <div class="page-top">
    <header class="page-header">
      <div class="header-left">
        <button class="back-btn" type="button" @click="goBack">返回问答</button>
        <h1>文献检索</h1>
      </div>
      <p class="subtitle">支持 DOI 精确检索与主题全文语义检索（基于向量库正文分块），结果可查看详情并打开 MinIO PDF。</p>
    </header>

    <section class="search-panel">
      <div class="search-row">
        <input
          v-model="query"
          class="search-input"
          type="search"
          placeholder="输入 DOI 或检索主题，如：磷酸铁锂 离子传导"
          @keyup.enter="runSearch"
        />
        <button class="primary-btn" type="button" :disabled="loading" @click="runSearch">
          {{ loading ? '检索中...' : '检索' }}
        </button>
      </div>

      <div class="options-grid">
        <label class="option-field">
          <span>查询类型</span>
          <select v-model="queryType">
            <option value="auto">自动识别</option>
            <option value="doi">DOI 精确</option>
            <option value="title">主题全文</option>
          </select>
        </label>

        <label v-if="queryType !== 'doi'" class="option-field">
          <span>匹配方式</span>
          <select v-model="matchMode">
            <option value="semantic">全文语义（推荐）</option>
            <option value="fuzzy">仅标题模糊</option>
            <option value="exact">仅标题精确</option>
          </select>
        </label>
      </div>
    </section>

    <p v-if="error" class="error-text">{{ error }}</p>
    <p v-else-if="resultMeta.count > 0" class="meta-text">
      共 {{ resultMeta.count }} 条结果
      <template v-if="resultMeta.query_type_detected">（识别为 {{ queryTypeDetectedLabel(resultMeta.query_type_detected) }}）</template>
    </p>
    </div>

    <div class="content-grid">
      <section class="results-panel">
        <h2>检索结果</h2>
        <div class="panel-scroll">
          <div v-if="loading" class="loading-animation">
            <div class="loading-spinner" aria-hidden="true">
              <span class="loading-dot" />
              <span class="loading-dot" />
              <span class="loading-dot" />
            </div>
            <span>检索中，请稍候...</span>
          </div>
          <p v-else-if="results.length === 0" class="hint">暂无结果</p>
          <ul v-else class="result-list">
          <li
            v-for="item in results"
            :key="item.doi"
            :class="['result-item', { active: item.doi === selectedDoi }]"
          >
            <button class="result-btn" type="button" @click="selectResult(item)">
              <span class="result-title">{{ item.title || item.doi }}</span>
              <span class="result-doi">{{ item.doi }}</span>
              <span class="result-tags">
                <span
                  v-if="shouldShowRelevance(resultMeta, matchMode) && relevanceLabel(item)"
                  class="tag score"
                  title="列表内相对相关度，不代表绝对置信概率"
                >
                  {{ relevanceLabel(item) }}
                </span>
                <span class="tag">{{ item.match_source }}</span>
                <span class="tag">{{ item.match_mode }}</span>
                <span v-if="item.pdf_exists" class="tag ok">PDF 可用</span>
                <span v-else class="tag warn">无 PDF</span>
              </span>
            </button>
            <button
              class="text-btn"
              type="button"
              :disabled="!item.pdf_exists"
              @click="openPdf(item.doi)"
            >
              打开 PDF
            </button>
          </li>
          </ul>
        </div>
      </section>

      <section class="detail-panel">
        <h2>文献详情</h2>
        <div ref="detailScrollRef" class="panel-scroll">
          <p v-if="detailLoading" class="hint">正在加载...</p>
          <p v-else-if="detailError" class="error-text">{{ detailError }}</p>
          <p v-else-if="!detail" class="hint">选择一条结果查看详情</p>
          <div v-else class="detail-body">
            <h3>{{ detail.title || selectedDoi }}</h3>
            <p class="meta-line">DOI: {{ detail.doi || selectedDoi }}</p>
            <p class="meta-line">作者: {{ detail.authors || '-' }}</p>
            <p class="meta-line">期刊: {{ detail.journal || '-' }}</p>
            <p class="meta-line">日期: {{ detail.publication_date || '-' }}</p>
            <p class="abstract">{{ detail.abstract || '无摘要信息' }}</p>
            <div v-if="detail.content" class="detail-content">
              <h4>正文摘录</h4>
              <MarkdownRenderer :content="detail.content" />
            </div>
          </div>
        </div>
      </section>
    </div>

    <PdfReader ref="pdfReader" />
  </div>
</template>

<style scoped>
.literature-search-page {
  display: flex;
  flex-direction: column;
  height: 100vh;
  height: 100dvh;
  overflow: hidden;
  background: #f8fafc;
  padding: 24px;
  box-sizing: border-box;
}

.page-top {
  flex-shrink: 0;
}

.page-header {
  margin-bottom: 20px;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 16px;
}

.page-header h1 {
  margin: 0;
  font-size: 24px;
  color: #1f2937;
}

.subtitle {
  margin: 8px 0 0;
  color: #64748b;
  font-size: 14px;
}

.back-btn,
.primary-btn,
.text-btn {
  border-radius: 6px;
  cursor: pointer;
  font-size: 14px;
}

.back-btn,
.text-btn {
  border: 1px solid #667eea;
  background: #fff;
  color: #667eea;
  padding: 8px 14px;
}

.primary-btn {
  border: none;
  background: #667eea;
  color: #fff;
  padding: 10px 18px;
}

.primary-btn:disabled {
  opacity: 0.7;
  cursor: not-allowed;
}

.search-panel {
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 16px;
  margin-bottom: 16px;
}

.search-row {
  display: flex;
  gap: 12px;
}

.search-input {
  flex: 1;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 15px;
}

.options-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
  margin-top: 14px;
}

.option-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
  color: #475569;
}

.option-field select {
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  padding: 8px 10px;
  background: #fff;
}

.content-grid {
  display: grid;
  grid-template-columns: minmax(320px, 1.1fr) minmax(280px, 0.9fr);
  gap: 16px;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.results-panel,
.detail-panel {
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 16px;
}

.results-panel h2,
.detail-panel h2 {
  flex-shrink: 0;
  margin: 0 0 12px;
  font-size: 18px;
}

.panel-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
}

.result-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.result-item {
  display: flex;
  gap: 8px;
  align-items: stretch;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  overflow: hidden;
}

.result-item.active {
  border-color: #667eea;
  box-shadow: 0 0 0 1px #667eea inset;
}

.result-btn {
  flex: 1;
  text-align: left;
  background: transparent;
  border: none;
  padding: 12px;
  cursor: pointer;
}

.result-title {
  display: block;
  font-weight: 600;
  color: #111827;
}

.result-doi,
.result-meta {
  display: block;
  margin-top: 4px;
  font-size: 12px;
  color: #64748b;
}

.result-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.tag {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 999px;
  background: #eef2ff;
  color: #4338ca;
}

.tag.ok {
  background: #ecfdf5;
  color: #047857;
}

.tag.score {
  background: #fff7ed;
  color: #c2410c;
}

.tag.warn {
  background: #fff7ed;
  color: #c2410c;
}

.detail-body h3 {
  margin: 0 0 8px;
}

.meta-line,
.abstract,
.hint,
.meta-text,
.error-text {
  font-size: 14px;
  line-height: 1.6;
}

.hint,
.meta-text {
  color: #64748b;
}

.error-text {
  color: #dc2626;
}

.abstract {
  margin-top: 12px;
  color: #334155;
}

.detail-content {
  margin-top: 16px;
}

@media (max-width: 960px) {
  .literature-search-page {
    height: auto;
    min-height: 100vh;
    min-height: 100dvh;
    overflow: visible;
  }

  .content-grid {
    grid-template-columns: 1fr;
    flex: none;
    overflow: visible;
  }

  .results-panel,
  .detail-panel {
    max-height: 50vh;
  }

  .search-row {
    flex-direction: column;
  }
}
</style>
