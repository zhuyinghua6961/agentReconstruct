<script setup>
import { computed, ref } from 'vue'
import { adminApi } from '../services/admin'

defineProps({
  show: Boolean,
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
    type: selectedFile.value.name.split('.').pop().toLowerCase(),
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
    await adminApi.downloadPersonnelImportTemplate(format)
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
    const result = await adminApi.batchImportPersonnel(selectedFile.value)
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
        <h3>批量导入人员</h3>
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
          <p class="hint">模板列固定为 employee_no、full_name、verification_code、status、remarks。</p>
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
              style="display: none"
              @change="handleFileSelect"
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
            <li><code>employee_no</code>、<code>full_name</code>、<code>verification_code</code> 必填。</li>
            <li><code>status</code> 只能填写 <code>active</code> 或 <code>disabled</code>。</li>
            <li><code>remarks</code> 可留空，用于记录备注说明。</li>
            <li>同一文件内重复的 employee_no 会导致整次导入失败。</li>
            <li>数据库里已存在的 employee_no 会按导入值覆盖更新。</li>
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

.modal-header,
.modal-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 20px 24px;
  border-bottom: 1px solid #e5e7eb;
}

.modal-footer {
  border-bottom: none;
  border-top: 1px solid #e5e7eb;
  justify-content: flex-end;
  gap: 12px;
}

.modal-body {
  padding: 24px;
}

.close-btn,
.template-btn,
.btn-secondary,
.btn-primary,
.remove-file-btn {
  cursor: pointer;
}

.template-buttons,
.info-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.upload-area {
  border: 2px dashed #d1d5db;
  border-radius: 12px;
  padding: 24px;
  text-align: center;
}

.upload-area.dragging {
  border-color: #2563eb;
  background: #eff6ff;
}

.upload-area.disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.file-info-display {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.alert-error {
  color: #b91c1c;
}
</style>
