import { watch } from 'vue';
import { buildPdfViewUrl } from '../../../api/literature';
import { buildUrl } from '../../../api/http';
import { useReferenceInspector } from './useReferenceInspector';
import { useSessionReferences } from './useSessionReferences';

function normalizeMaybeRelativeUrl(url) {
  const text = String(url || '').trim();
  if (!text) {
    return '';
  }
  if (text.startsWith('http://') || text.startsWith('https://')) {
    return text;
  }
  return buildUrl(text);
}

function appendPdfToken(url) {
  const value = String(url || '').trim();
  if (!value || !value.includes('/view_pdf/')) {
    return value;
  }
  const token = window.localStorage.getItem('token')
    || window.localStorage.getItem('agentcode.auth.token.v1')
    || '';
  if (!token || value.includes('token=')) {
    return value;
  }
  return `${value}${value.includes('?') ? '&' : '?'}token=${encodeURIComponent(token)}`;
}

export function useReferencePanelState(activeSession) {
  const { metadataText, referenceDois, referencePdfMap } = useSessionReferences(activeSession);
  const {
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
  } = useReferenceInspector();

  function getPdfUrl(doi) {
    const previewUrl = appendPdfToken(normalizeMaybeRelativeUrl(previewByDoi.value?.[doi]?.pdfUrl));
    if (previewUrl) {
      return previewUrl;
    }
    const sessionUrl = appendPdfToken(normalizeMaybeRelativeUrl(referencePdfMap.value[doi]));
    if (sessionUrl) {
      return sessionUrl;
    }
    return buildPdfViewUrl(doi);
  }

  watch(
    referenceDois,
    async (refs) => {
      await Promise.all([syncSelection(refs), loadPreviews(refs)]);
    },
    { immediate: true }
  );

  return {
    metadataText,
    referenceDois,
    selectedDoi,
    literatureDetail,
    loadingReference,
    referenceError,
    previewByDoi,
    loadingPreviews,
    previewError,
    previewMeta,
    setSelectedDoi,
    reloadSelectedDetail,
    getPdfUrl,
  };
}
