import { computed } from 'vue';

export function useSessionReferences(activeSession) {
  const metadataText = computed(() => {
    const mode = activeSession.value?.metadata?.queryMode;
    const refs = activeSession.value?.metadata?.references || [];
    if (!mode && refs.length === 0) {
      return '无';
    }
    return `${mode || '未知模式'} | 引用: ${refs.length}`;
  });

  const referenceDois = computed(() => {
    const refs = activeSession.value?.metadata?.references || [];
    const unique = new Set();
    for (const item of refs) {
      const doi = String(item || '').trim();
      if (doi) {
        unique.add(doi);
      }
    }
    return Array.from(unique);
  });

  const referencePdfMap = computed(() => {
    const links = activeSession.value?.metadata?.pdfLinks || [];
    const map = {};
    for (const item of links) {
      const doi = String(item?.doi || '').trim();
      const url = String(item?.pdfUrl || '').trim();
      if (doi && url) {
        map[doi] = url;
      }
    }
    return map;
  });

  return {
    metadataText,
    referenceDois,
    referencePdfMap,
  };
}
