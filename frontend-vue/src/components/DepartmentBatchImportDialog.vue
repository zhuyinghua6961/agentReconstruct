<script setup>
import { computed, ref } from 'vue'
import { adminApi } from '../services/admin'

defineProps({
  show: Boolean
})

const emit = defineEmits(['close', 'import-success'])

const selectedFile = ref(null)
const isDragging = ref(false)
const uploading = ref(false)
const error = ref('')
const fileInput = ref(null)

const fileInfo = computed(() => {
  if (!selectedFile.value) return null
  return {
    name: selectedFile.value.name,
    size: formatFileSize(selectedFile.value.size),
    type: selectedFile.value.name.split('.').pop().toLowerCase()
  }
})

function formatFileSize(bytes) {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${Math.round((bytes / Math.pow(k, i)) * 100) / 100} ${sizes[i]}`
}

function validateFile(file) {
  error.value = ''
  if (!file?.name?.match(/\.(xlsx|csv)$/i)) {
    error.value = '只支持 .xlsx 和 .csv 格式的文件'
    return false
  }
  return true
}

function handleFileSelect(event) {
  const file = event.target.files[0]
  if (file && validateFile(file)) {
    selectedFile.value = file
    return
  }
  if (event?.target) {
    event.target.value = ''
  }
}

function triggerFileSelect() {
  if (uploading.value) return
  fileInput.value?.click()
}

function handleDragOver(event) {
  event.preventDefault()
  isDragging.value = true
}

function handleDragLeave() {
  isDragging.value = false
}

function handleDrop(event) {
  event.preventDefault()
  isDragging.value = false
  if (uploading.value) return
  const file = event.dataTransfer.files[0]
  if (file && validateFile(file)) {
    selectedFile.value = file
  }
}

function resetFileInput() {
  if (fileInput.value) {
    fileInput.value.value = ''
  }
}

function clearSelectedFile() {
  selectedFile.value = null
  resetFileInput()
}

async function downloadTemplate(format) {
  try {
    await adminApi.downloadDepartmentImportTemplate(format)
  } catch (err) {
    error.value = err?.message || '下载模板失败'
  }
}

async function startImport() {
  if (!selectedFile.value) {
    error.value = '请先选择文件'
    return
  }

  uploading.value = true
  error.value = ''
  try {
    const result = await adminApi.batchImportDepartments(selectedFile.value)
    if (result.success) {
      emit('import-success', result.data)
      close()
      return
    }
    error.value = result.error || '导入失败'
  } catch (err) {
    error.value = err?.message || '导入失败，请稍后重试'
  } finally {
    uploading.value = false
  }
}

function close() {
  clearSelectedFile()
  error.value = ''
  emit('close')
}

function requestClose() {
  if (uploading.value) {
    return
  }
  close()
}
</script>

<template>
  <div v-if="show" class="modal-overlay" @click.self="requestClose">
    <div class="modal">
      <div class="modal-header">
        <h3>批量导入部门</h3>
        <button class="close-btn" :disabled="uploading" @click="requestClose">x</button>
      </div>

      <div class="modal-body">
        <div v-if="error" class="alert alert-error">{{ error }}</div>

        <div class="template-section">
          <p class="section-title">第一步：下载模板</p>
          <div class="template-buttons">
            <button class="template-btn" @click="downloadTemplate('xlsx')">下载 Excel 模板</button>
            <button class="template-btn" @click="downloadTemplate('csv')">下载 CSV 模板</button>
          </div>
          <p class="hint">模板列固定为 primary_department_name、primary_status、secondary_department_name、secondary_status。</p>
        </div>

        <div class="upload-section">
          <p class="section-title">第二步：上传填写后的文件</p>
          <div
            class="upload-area"
            :class="{ dragging: isDragging, 'has-file': selectedFile, disabled: uploading }"
            @dragover="handleDragOver"
            @dragleave="handleDragLeave"
            @drop="handleDrop"
            @click="triggerFileSelect"
          >
            <input
              ref="fileInput"
              type="file"
              accept=".xlsx,.csv"
              @change="handleFileSelect"
              style="display: none"
            >

            <div v-if="!selectedFile" class="upload-placeholder">
              <p class="upload-text">点击选择文件或拖拽文件到此处</p>
              <p class="upload-hint">支持 .xlsx 和 .csv 格式</p>
            </div>

            <div v-else class="file-info-display">
              <div class="file-details">
                <p class="file-name">{{ fileInfo.name }}</p>
                <p class="file-meta">{{ fileInfo.size }} · {{ fileInfo.type.toUpperCase() }}</p>
              </div>
              <button class="remove-file-btn" :disabled="uploading" @click.stop="clearSelectedFile">x</button>
            </div>
          </div>
        </div>

        <div class="info-section">
          <p class="info-title">导入说明：</p>
          <ul class="info-list">
            <li>每一行都必须填写 primary_department_name、primary_status、secondary_department_name、secondary_status。</li>
            <li>状态值只能填写 <code>active</code> 或 <code>disabled</code>。</li>
            <li>同名一级部门会按名称更新状态；同名二级部门会在所属一级部门下按名称更新状态。</li>
            <li>导入文件里没有出现的已有部门会保留原样，不会被删除，也不会被自动停用。</li>
            <li>同一一级部门在同一文件中的 primary_status 必须保持一致，否则冲突行会失败。</li>
            <li>完全重复的行会被标记为跳过。</li>
          </ul>
        </div>
      </div>

      <div class="modal-footer">
        <button class="btn-secondary" :disabled="uploading" @click="requestClose">取消</button>
        <button class="btn-primary" :disabled="!selectedFile || uploading" @click="startImport">
          <span v-if="uploading">导入中...</span>
          <span v-else>开始导入</span>
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.modal {
  background: white;
  border-radius: 12px;
  width: 100%;
  max-width: 600px;
  max-height: 90vh;
  overflow-y: auto;
  box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.1);
}

.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 20px 24px;
  border-bottom: 1px solid #e5e7eb;
}

.modal-header h3 {
  font-size: 18px;
  color: #1f2937;
  margin: 0;
}

.close-btn {
  background: none;
  border: none;
  font-size: 24px;
  color: #9ca3af;
  cursor: pointer;
  width: 32px;
  height: 32px;
  border-radius: 6px;
}

.close-btn:hover {
  background: #f3f4f6;
  color: #374151;
}

.modal-body {
  padding: 24px;
}

.alert {
  padding: 12px 16px;
  border-radius: 8px;
  margin-bottom: 20px;
}

.alert-error {
  background: #fef2f2;
  color: #dc2626;
  border: 1px solid #fecaca;
}

.template-section,
.upload-section,
.info-section {
  margin-bottom: 24px;
}

.section-title {
  font-size: 14px;
  font-weight: 500;
  color: #374151;
  margin: 0 0 12px 0;
}

.template-buttons {
  display: flex;
  gap: 12px;
}

.template-btn {
  flex: 1;
  padding: 12px 16px;
  background: #f9fafb;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  cursor: pointer;
  font-size: 14px;
  color: #374151;
}

.template-btn:hover {
  background: #f3f4f6;
  border-color: #d1d5db;
}

.hint {
  font-size: 12px;
  color: #6b7280;
  margin: 10px 0 0 0;
}

.upload-area {
  border: 2px dashed #d1d5db;
  border-radius: 12px;
  padding: 28px 20px;
  text-align: center;
  cursor: pointer;
  transition: all 0.2s;
}

.upload-area.dragging {
  border-color: #667eea;
  background: #eef2ff;
}

.upload-area.has-file {
  border-style: solid;
  background: #f9fafb;
}

.upload-area.disabled {
  cursor: not-allowed;
  opacity: 0.7;
}

.upload-text,
.upload-hint,
.info-title,
.file-name,
.file-meta {
  margin: 0;
}

.upload-text,
.info-title,
.file-name {
  color: #1f2937;
}

.upload-hint,
.file-meta {
  font-size: 13px;
  color: #6b7280;
}

.file-info-display {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.file-details {
  text-align: left;
  min-width: 0;
}

.remove-file-btn {
  border: none;
  border-radius: 999px;
  background: #fee2e2;
  color: #b91c1c;
  width: 28px;
  height: 28px;
  cursor: pointer;
  flex-shrink: 0;
}

.close-btn:disabled,
.remove-file-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.info-list {
  margin: 0;
  padding-left: 20px;
  color: #374151;
  font-size: 14px;
  line-height: 1.7;
}

.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  padding: 20px 24px;
  border-top: 1px solid #e5e7eb;
}

.btn-secondary,
.btn-primary {
  border: 1px solid #d1d5db;
  border-radius: 8px;
  padding: 10px 18px;
  cursor: pointer;
}

.btn-secondary {
  background: white;
  color: #374151;
}

.btn-primary {
  background: #0f766e;
  border-color: #0f766e;
  color: white;
}

.btn-primary:disabled,
.btn-secondary:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

@media (max-width: 640px) {
  .modal {
    max-width: calc(100vw - 24px);
  }

  .template-buttons,
  .modal-footer {
    flex-direction: column;
  }
}
</style>
