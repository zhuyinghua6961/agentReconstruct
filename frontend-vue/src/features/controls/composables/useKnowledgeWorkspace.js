import { ref } from 'vue';
import { clearCache, clearPdf, getKbInfo, refreshKb, uploadExcel, uploadPdf } from '../../../api/chat';

export function useKnowledgeWorkspace() {
  const kbInfo = ref({});
  const statusLine = ref('');
  const usePdf = ref(false);
  const pdfPath = ref('');
  const pdfName = ref('');
  const uploading = ref(false);

  function setStatus(text) {
    statusLine.value = String(text || '');
  }

  function clearStatus() {
    statusLine.value = '';
  }

  async function loadKb() {
    try {
      kbInfo.value = await getKbInfo();
    } catch (error) {
      setStatus(`获取知识库信息失败: ${String(error)}`);
    }
  }

  async function onRefreshKb() {
    try {
      const resp = await refreshKb();
      setStatus(resp.message || '知识库已刷新');
      await loadKb();
    } catch (error) {
      setStatus(`刷新失败: ${String(error)}`);
    }
  }

  async function onClearCache() {
    try {
      const resp = await clearCache();
      setStatus(resp.message || '缓存已清空');
    } catch (error) {
      setStatus(`清缓存失败: ${String(error)}`);
    }
  }

  async function onUploadPdf(file, conversationId = null) {
    uploading.value = true;
    try {
      const resp = await uploadPdf(file, conversationId);
      if (resp.error) {
        setStatus(resp.error);
        return resp;
      }
      pdfPath.value = resp.filepath || '';
      pdfName.value = resp.filename || file.name;
      usePdf.value = true;
      setStatus(resp.message || 'PDF 上传成功');
      return resp;
    } catch (error) {
      setStatus(`上传 PDF 失败: ${String(error)}`);
      return null;
    } finally {
      uploading.value = false;
    }
  }

  async function onUploadExcel(file, conversationId = null) {
    uploading.value = true;
    try {
      const resp = await uploadExcel(file, conversationId);
      setStatus(resp.error || resp.message || '表格上传完成');
      return resp;
    } catch (error) {
      setStatus(`上传表格失败: ${String(error)}`);
      return null;
    } finally {
      uploading.value = false;
    }
  }

  async function onClearPdf() {
    try {
      const resp = await clearPdf();
      pdfPath.value = '';
      pdfName.value = '';
      usePdf.value = false;
      setStatus(resp.message || 'PDF 上下文已清除');
    } catch (error) {
      setStatus(`清除 PDF 失败: ${String(error)}`);
    }
  }

  function onToggleUsePdf(value) {
    usePdf.value = Boolean(value);
  }

  return {
    kbInfo,
    statusLine,
    usePdf,
    pdfPath,
    pdfName,
    uploading,
    setStatus,
    clearStatus,
    loadKb,
    onRefreshKb,
    onClearCache,
    onUploadPdf,
    onUploadExcel,
    onClearPdf,
    onToggleUsePdf,
  };
}
