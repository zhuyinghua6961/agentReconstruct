<script setup>
import MarkdownRenderer from '../../markdown/MarkdownRenderer.vue';

const props = defineProps({
  references: { type: Array, default: () => [] },
  selectedDoi: { type: String, default: '' },
  detail: { type: Object, default: null },
  previewByDoi: { type: Object, default: () => ({}) },
  previewMeta: {
    type: Object,
    default: () => ({ requestedCount: 0, maxItems: 30, truncated: false }),
  },
  loading: { type: Boolean, default: false },
  loadingPreviews: { type: Boolean, default: false },
  error: { type: String, default: '' },
  previewError: { type: String, default: '' },
  getPdfUrl: { type: Function, required: true },
});

const emit = defineEmits(['select', 'refresh', 'open-doi', 'open-patent']);

function openPdf(doi) {
  return props.getPdfUrl(doi);
}

function getPreview(doi) {
  return props.previewByDoi?.[doi] || null;
}
</script>

<template>
  <section class="reference-panel">
    <div class="reference-head">
      <h2>引用文献</h2>
      <span class="count">{{ references.length }}</span>
    </div>

    <p v-if="references.length === 0" class="hint">当前回答暂无 DOI 引用</p>

    <template v-else>
      <p v-if="loadingPreviews" class="hint">正在加载引用预览...</p>
      <p v-else-if="previewError" class="hint ref-error">{{ previewError }}</p>
      <p v-else-if="previewMeta?.truncated" class="hint">
        引用过多，仅展示前 {{ previewMeta?.maxItems || 30 }} 条（共 {{ previewMeta?.requestedCount || references.length }} 条）
      </p>

      <ul class="ref-list">
        <li v-for="doi in references" :key="doi" :class="['ref-item', { active: doi === selectedDoi }]">
          <button class="ref-btn" @click="emit('select', doi)">
            <span class="doi">{{ doi }}</span>
            <span v-if="getPreview(doi)?.title" class="ref-title">{{ getPreview(doi)?.title }}</span>
            <span v-if="getPreview(doi)?.journal" class="ref-meta">{{ getPreview(doi)?.journal }}</span>
          </button>
          <a
            class="text-btn"
            :class="{ disabled: getPreview(doi) && !getPreview(doi)?.pdfExists }"
            :href="openPdf(doi)"
            target="_blank"
            rel="noreferrer"
          >PDF</a>
        </li>
      </ul>

      <section class="ref-detail">
        <div class="detail-head">
          <h3>{{ selectedDoi || '文献详情' }}</h3>
          <button class="text-btn" :disabled="!selectedDoi || loading" @click="emit('refresh')">
            刷新
          </button>
        </div>

        <p v-if="loading" class="hint">正在加载文献内容...</p>
        <p v-else-if="error" class="hint ref-error">{{ error }}</p>

        <div v-else-if="detail" class="detail-body">
          <h4 class="paper-title">{{ detail.title || selectedDoi }}</h4>
          <p class="meta-line">作者: {{ detail.authors || '-' }}</p>
          <p class="meta-line">期刊: {{ detail.journal || '-' }}</p>
          <p class="meta-line">日期: {{ detail.publication_date || '-' }}</p>
          <p class="abstract">{{ detail.abstract || '无摘要信息' }}</p>
          <div v-if="detail.content" class="content-box">
            <MarkdownRenderer
              :content="detail.content"
              variant="document"
              @open-doi="emit('open-doi', $event)"
              @open-patent="emit('open-patent', $event)"
            />
          </div>
        </div>
      </section>
    </template>
  </section>
</template>
