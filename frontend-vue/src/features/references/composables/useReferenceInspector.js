import { ref } from 'vue';
import { getLiteratureContent, getReferencePreview } from '../../../api/literature';

export function useReferenceInspector() {
  const selectedDoi = ref('');
  const literatureDetail = ref(null);
  const loadingReference = ref(false);
  const referenceError = ref('');
  const previewByDoi = ref({});
  const loadingPreviews = ref(false);
  const previewError = ref('');
  const previewMeta = ref({ requestedCount: 0, maxItems: 30, truncated: false });
  const detailCache = new Map();
  let detailRequestSeq = 0;
  let previewRequestSeq = 0;
  let lastPreviewKey = '';

  async function loadByDoi(doi, options = {}) {
    const forceRefresh = Boolean(options?.forceRefresh);
    const cleanDoi = String(doi || '').trim();
    if (!cleanDoi) {
      selectedDoi.value = '';
      literatureDetail.value = null;
      referenceError.value = '';
      return;
    }

    selectedDoi.value = cleanDoi;
    loadingReference.value = true;
    referenceError.value = '';
    const seq = ++detailRequestSeq;

    try {
      if (!forceRefresh && detailCache.has(cleanDoi)) {
        literatureDetail.value = detailCache.get(cleanDoi);
        return;
      }
      const payload = await getLiteratureContent(cleanDoi);
      if (seq !== detailRequestSeq) {
        return;
      }
      if (payload?.error) {
        throw new Error(payload.error);
      }
      detailCache.set(cleanDoi, payload);
      literatureDetail.value = payload;
    } catch (error) {
      if (seq !== detailRequestSeq) {
        return;
      }
      literatureDetail.value = null;
      referenceError.value = `文献详情加载失败: ${String(error)}`;
    } finally {
      if (seq === detailRequestSeq) {
        loadingReference.value = false;
      }
    }
  }

  async function syncSelection(references) {
    const safeList = Array.isArray(references)
      ? references.map((item) => String(item || '').trim()).filter(Boolean)
      : [];

    if (safeList.length === 0) {
      selectedDoi.value = '';
      literatureDetail.value = null;
      referenceError.value = '';
      loadingReference.value = false;
      previewByDoi.value = {};
      previewError.value = '';
      loadingPreviews.value = false;
      previewMeta.value = { requestedCount: 0, maxItems: 30, truncated: false };
      lastPreviewKey = '';
      previewRequestSeq += 1;
      return;
    }

    if (!safeList.includes(selectedDoi.value)) {
      await loadByDoi(safeList[0]);
    }
  }

  async function loadPreviews(references) {
    const safeList = Array.isArray(references)
      ? references.map((item) => String(item || '').trim()).filter(Boolean)
      : [];
    if (safeList.length === 0) {
      previewByDoi.value = {};
      previewError.value = '';
      loadingPreviews.value = false;
      previewMeta.value = { requestedCount: 0, maxItems: 30, truncated: false };
      lastPreviewKey = '';
      previewRequestSeq += 1;
      return;
    }

    const key = safeList.join('|');
    if (key === lastPreviewKey && Object.keys(previewByDoi.value).length > 0) {
      return;
    }

    loadingPreviews.value = true;
    previewError.value = '';
    const seq = ++previewRequestSeq;
    try {
      const payload = await getReferencePreview(safeList, { maxItems: 40 });
      if (seq !== previewRequestSeq) {
        return;
      }
      const items = Array.isArray(payload?.items) ? payload.items : [];
      const map = {};
      for (const item of items) {
        const doi = String(item?.doi || '').trim();
        if (!doi) {
          continue;
        }
        map[doi] = {
          doi,
          title: String(item?.title || ''),
          journal: String(item?.journal || ''),
          publicationDate: String(item?.publication_date || ''),
          source: String(item?.source || ''),
          pdfExists: Boolean(item?.pdf_exists),
          pdfUrl: String(item?.pdf_url || ''),
        };
      }
      previewByDoi.value = map;
      previewMeta.value = {
        requestedCount: Number(payload?.requested_count) || safeList.length,
        maxItems: Number(payload?.max_items) || 30,
        truncated: Boolean(payload?.truncated),
      };
      lastPreviewKey = key;
    } catch (error) {
      if (seq !== previewRequestSeq) {
        return;
      }
      previewByDoi.value = {};
      previewMeta.value = { requestedCount: safeList.length, maxItems: 30, truncated: false };
      previewError.value = `引用预览加载失败: ${String(error)}`;
    } finally {
      if (seq === previewRequestSeq) {
        loadingPreviews.value = false;
      }
    }
  }

  async function setSelectedDoi(doi) {
    await loadByDoi(doi);
  }

  async function reloadSelectedDetail() {
    if (!selectedDoi.value) {
      return;
    }
    await loadByDoi(selectedDoi.value, { forceRefresh: true });
  }

  return {
    selectedDoi,
    literatureDetail,
    loadingReference,
    referenceError,
    previewByDoi,
    loadingPreviews,
    previewError,
    previewMeta,
    setSelectedDoi,
    syncSelection,
    loadPreviews,
    reloadSelectedDetail,
  };
}
