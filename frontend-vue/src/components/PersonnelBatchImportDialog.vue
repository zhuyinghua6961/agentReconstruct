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
        <button class="close-btn" :disabled="uploading" @click="requestClose">×</button>
      </div>

      <div class="modal-body">
        <div v-if="error" class="alert alert-error">{{ error }}</div>

        <div class="template-section">
          <p class="section-title">第一步：下载模板</p>
          <div class="template-buttons">
            <button class="template-btn" @click="downloadTemplate('xlsx')">
              <span class="icon">📊</span>
              下载 Excel 模板
            </button>
            <button class="template-btn" @click="downloadTemplate('csv')">
              <span class="icon">📄</span>
              下载 CSV 模板
            </button>
          </div>
          <p class="hint">模板列为工号、姓名、一级部门、二级部门、三级部门、校验码、备注；一级部门必填，二级、三级部门可留空；部门不存在时会自动创建，停用部门不会自动启用；系统同时兼容旧英文列名，导入后人员状态默认启用。</p>
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
              <span class="upload-icon">📁</span>
              <p class="upload-text">点击选择文件或拖拽文件到此处</p>
              <p class="upload-hint">支持 .xlsx 和 .csv 格式</p>
            </div>

            <div v-else class="file-info-display">
              <span class="file-icon">📄</span>
              <div class="file-details">
                <p class="file-name">{{ fileInfo.name }}</p>
                <p class="file-meta">{{ fileInfo.size }} · {{ fileInfo.type.toUpperCase() }}</p>
              </div>
              <button class="remove-file-btn" :disabled="uploading" @click.stop="clearSelectedFile">×</button>
            </div>
          </div>
        </div>

        <div class="info-section">
          <p class="info-title">📌 导入说明：</p>
          <ul class="info-list">
            <li>工号、姓名、一级部门、校验码必填；二级部门和三级部门可按实际管理层级留空。</li>
            <li>部门不存在时会自动创建对应的一级、二级或三级部门。</li>
            <li>停用部门不会自动启用，导入到停用部门会失败。</li>
            <li>也兼容旧列名 employee_no、full_name、primary_department_name、secondary_department_name、tertiary_department_name、verification_code。</li>
            <li>备注可留空，用于记录补充说明。</li>
            <li>模板不包含状态列，新导入和更新的人员状态默认启用。</li>
            <li>同一文件内重复的工号会导致整次导入失败。</li>
            <li>工号作为匹配键：工号相同且内容一致会显示跳过。</li>
            <li>工号相同但姓名、部门、校验码、备注等其他信息变化会显示更新。</li>
            <li>工号变化会作为新人员创建，不会关联到原人员。</li>
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

.close-btn {
  background: none;
  border: none;
  font-size: 28px;
  color: #9ca3af;
  cursor: pointer;
  padding: 0;
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
}

.close-btn:hover:not(:disabled) {
  background: #f3f4f6;
  color: #374151;
}

.close-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
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
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 12px 16px;
  background: #f9fafb;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  cursor: pointer;
  font-size: 14px;
  color: #374151;
  transition: all 0.2s;
}

.template-btn:hover {
  background: #f3f4f6;
  border-color: #d1d5db;
}

.template-btn .icon {
  font-size: 18px;
}

.hint {
  font-size: 12px;
  color: #6b7280;
  margin: 8px 0 0 0;
}

.upload-area {
  border: 2px dashed #d1d5db;
  border-radius: 8px;
  padding: 32px;
  text-align: center;
  cursor: pointer;
  transition: all 0.2s;
  background: #fafafa;
}

.upload-area:hover {
  border-color: #667eea;
  background: #f9fafb;
}

.upload-area.dragging {
  border-color: #667eea;
  background: #eef2ff;
}

.upload-area.has-file {
  padding: 16px;
  background: #f9fafb;
  border-style: solid;
}

.upload-area.disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.upload-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
}

.upload-icon {
  font-size: 48px;
}

.upload-text {
  font-size: 14px;
  color: #374151;
  margin: 0;
}

.upload-hint {
  font-size: 12px;
  color: #6b7280;
  margin: 0;
}

.file-info-display {
  display: flex;
  align-items: center;
  gap: 12px;
}

.file-icon {
  font-size: 32px;
}

.file-details {
  flex: 1;
  min-width: 0;
  text-align: left;
}

.file-name {
  font-size: 14px;
  color: #1f2937;
  margin: 0 0 4px 0;
  font-weight: 500;
  overflow-wrap: anywhere;
}

.file-meta {
  font-size: 12px;
  color: #6b7280;
  margin: 0;
}

.remove-file-btn {
  background: #fee2e2;
  border: none;
  color: #dc2626;
  font-size: 20px;
  width: 28px;
  height: 28px;
  border-radius: 6px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}

.remove-file-btn:hover:not(:disabled) {
  background: #fecaca;
}

.remove-file-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.info-section {
  background: #eff6ff;
  border: 1px solid #bfdbfe;
  border-radius: 8px;
  padding: 16px;
}

.info-title {
  font-size: 13px;
  font-weight: 500;
  color: #1e40af;
  margin: 0 0 8px 0;
}

.info-list {
  margin: 0;
  padding-left: 20px;
  font-size: 12px;
  color: #1e40af;
  line-height: 1.6;
}

.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  padding: 16px 24px;
  border-top: 1px solid #e5e7eb;
}

.btn-primary,
.btn-secondary {
  padding: 10px 20px;
  border-radius: 6px;
  font-size: 14px;
  cursor: pointer;
  border: none;
  transition: all 0.2s;
}

.btn-primary {
  background: #667eea;
  color: white;
}

.btn-primary:hover:not(:disabled) {
  background: #5a67d8;
}

.btn-primary:disabled {
  background: #d1d5db;
  cursor: not-allowed;
}

.btn-secondary {
  background: #f3f4f6;
  color: #374151;
  border: 1px solid #d1d5db;
}

.btn-secondary:hover:not(:disabled) {
  background: #e5e7eb;
}

.btn-secondary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
