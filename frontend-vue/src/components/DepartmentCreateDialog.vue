<script setup>
import { computed, ref, watch } from 'vue'
import { adminApi } from '../services/admin'
import {
  DEFAULT_DEPARTMENT_CREATE_TARGET_LEVEL,
  buildDepartmentCreateState,
  cleanDepartmentName,
  normalizeDepartmentId,
} from '../utils/departmentCreateModel'

const props = defineProps({
  show: Boolean,
  departmentTree: {
    type: Array,
    default: () => [],
  },
})

const emit = defineEmits(['close', 'created', 'changed'])

const targetLevel = ref(DEFAULT_DEPARTMENT_CREATE_TARGET_LEVEL)
const primaryMode = ref('new')
const secondaryMode = ref('new')
const selectedPrimaryId = ref('')
const selectedSecondaryId = ref('')
const primaryName = ref('')
const secondaryName = ref('')
const tertiaryName = ref('')
const submitting = ref(false)
const error = ref('')

const targetOptions = [
  { value: 'primary', label: '一级部门' },
  { value: 'secondary', label: '二级部门' },
  { value: 'tertiary', label: '三级部门' },
]

const createState = computed(() => buildDepartmentCreateState({
  departmentTree: props.departmentTree,
  targetLevel: targetLevel.value,
  primaryMode: primaryMode.value,
  secondaryMode: secondaryMode.value,
  selectedPrimaryId: selectedPrimaryId.value,
  selectedSecondaryId: selectedSecondaryId.value,
  primaryName: primaryName.value,
  secondaryName: secondaryName.value,
  tertiaryName: tertiaryName.value,
  submitting: submitting.value,
}))

const primaryItems = computed(() => createState.value.departmentTree)
const secondaryItems = computed(() => createState.value.secondaryItems)
const needsParentPrimary = computed(() => createState.value.needsParentPrimary)
const needsTertiary = computed(() => createState.value.needsTertiary)
const canSelectPrimary = computed(() => createState.value.canSelectPrimary)
const canSelectSecondary = computed(() => createState.value.canSelectSecondary)
const canSubmit = computed(() => createState.value.canSubmit)

watch(
  () => props.show,
  () => {
    resetForm()
  },
  { immediate: true },
)

watch(targetLevel, (level) => {
  error.value = ''
  if (level === 'primary') {
    primaryMode.value = 'new'
    secondaryMode.value = 'new'
    selectedPrimaryId.value = ''
    selectedSecondaryId.value = ''
    secondaryName.value = ''
    tertiaryName.value = ''
    return
  }
  if (level === 'secondary') {
    secondaryMode.value = 'new'
    selectedSecondaryId.value = ''
    tertiaryName.value = ''
  }
})

watch(primaryMode, (mode) => {
  error.value = ''
  if (mode === 'new') {
    selectedPrimaryId.value = ''
    selectedSecondaryId.value = ''
    secondaryMode.value = 'new'
    return
  }
  primaryName.value = ''
})

watch(selectedPrimaryId, () => {
  selectedSecondaryId.value = ''
  if (!canSelectSecondary.value) {
    secondaryMode.value = 'new'
  }
})

watch(secondaryMode, (mode) => {
  error.value = ''
  if (mode === 'new') {
    selectedSecondaryId.value = ''
    return
  }
  secondaryName.value = ''
})

watch(canSelectPrimary, (selectable) => {
  if (!selectable) {
    primaryMode.value = 'new'
  }
})

watch(canSelectSecondary, (selectable) => {
  if (!selectable) {
    secondaryMode.value = 'new'
  }
})

function resetForm() {
  targetLevel.value = DEFAULT_DEPARTMENT_CREATE_TARGET_LEVEL
  primaryMode.value = 'new'
  secondaryMode.value = 'new'
  selectedPrimaryId.value = ''
  selectedSecondaryId.value = ''
  primaryName.value = ''
  secondaryName.value = ''
  tertiaryName.value = ''
  submitting.value = false
  error.value = ''
}

function requestClose() {
  if (submitting.value) {
    return
  }
  emit('close')
}

function requireText(value, message) {
  const text = cleanDepartmentName(value)
  if (!text) {
    throw new Error(message)
  }
  return text
}

function requireId(value, message) {
  const id = normalizeDepartmentId(value)
  if (id === null) {
    throw new Error(message)
  }
  return id
}

function readCreatedId(result, label) {
  const id = normalizeDepartmentId(result?.data?.id)
  if (id === null) {
    throw new Error(`${label}创建成功但未返回编号`)
  }
  return id
}

async function ensurePrimaryDepartmentId(createdRecords) {
  if (needsParentPrimary.value && primaryMode.value === 'existing') {
    return requireId(selectedPrimaryId.value, '请选择一级部门')
  }

  const name = requireText(primaryName.value, '请输入一级部门名称')
  const result = await adminApi.createPrimaryDepartment(name)
  if (!result.success) {
    throw new Error(result.error || '创建一级部门失败')
  }
  const id = readCreatedId(result, '一级部门')
  createdRecords.push({ level: 'primary', id, name })
  return id
}

async function ensureSecondaryDepartmentId(createdRecords, primaryDepartmentId) {
  if (needsTertiary.value && secondaryMode.value === 'existing') {
    return requireId(selectedSecondaryId.value, '请选择二级部门')
  }

  const name = requireText(secondaryName.value, '请输入二级部门名称')
  const result = await adminApi.createSecondaryDepartment(primaryDepartmentId, name)
  if (!result.success) {
    throw new Error(result.error || '创建二级部门失败')
  }
  const id = readCreatedId(result, '二级部门')
  createdRecords.push({ level: 'secondary', id, name })
  return id
}

async function createTertiaryDepartment(createdRecords, secondaryDepartmentId) {
  const name = requireText(tertiaryName.value, '请输入三级部门名称')
  const result = await adminApi.createTertiaryDepartment(secondaryDepartmentId, name)
  if (!result.success) {
    throw new Error(result.error || '创建三级部门失败')
  }
  const id = readCreatedId(result, '三级部门')
  createdRecords.push({ level: 'tertiary', id, name })
  return id
}

async function submit() {
  if (!canSubmit.value || submitting.value) {
    return
  }

  const createdRecords = []
  submitting.value = true
  error.value = ''

  try {
    const primaryId = await ensurePrimaryDepartmentId(createdRecords)
    if (targetLevel.value === 'primary') {
      emit('created', { targetLevel: 'primary', createdRecords, message: '一级部门创建成功' })
      return
    }

    const secondaryId = await ensureSecondaryDepartmentId(createdRecords, primaryId)
    if (targetLevel.value === 'secondary') {
      emit('created', { targetLevel: 'secondary', createdRecords, message: '二级部门创建成功' })
      return
    }

    await createTertiaryDepartment(createdRecords, secondaryId)
    emit('created', { targetLevel: 'tertiary', createdRecords, message: '三级部门创建成功' })
  } catch (err) {
    error.value = err?.message || '创建部门失败'
    if (createdRecords.length > 0) {
      emit('changed', { createdRecords })
    }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div v-if="show" class="modal-overlay" @click.self="requestClose">
    <div class="modal modal-wide" role="dialog" aria-modal="true">
      <div class="modal-header">
        <h3>添加部门</h3>
        <button class="close-btn" type="button" :disabled="submitting" @click="requestClose">x</button>
      </div>

      <div class="modal-body">
        <div v-if="error" class="alert alert-error">{{ error }}</div>

        <div class="form-group">
          <span>创建目标</span>
          <div class="segmented-control" role="radiogroup" aria-label="创建目标">
            <label
              v-for="option in targetOptions"
              :key="option.value"
              class="segment-option"
              :class="{ active: targetLevel === option.value }"
            >
              <input v-model="targetLevel" type="radio" :value="option.value">
              <span>{{ option.label }}</span>
            </label>
          </div>
        </div>

        <label v-if="!needsParentPrimary" class="form-group">
          <span>一级部门名称</span>
          <input v-model="primaryName" type="text" placeholder="请输入一级部门名称">
        </label>

        <template v-else>
          <div class="form-section">
            <span class="form-section-title">一级部门</span>
            <div class="source-options" role="radiogroup" aria-label="一级部门来源">
              <label
                class="source-radio"
                :class="{ active: primaryMode === 'existing', disabled: !canSelectPrimary }"
              >
                <input
                  v-model="primaryMode"
                  type="radio"
                  value="existing"
                  :disabled="!canSelectPrimary"
                >
                <span>已有一级部门</span>
              </label>
              <label class="source-radio" :class="{ active: primaryMode === 'new' }">
                <input v-model="primaryMode" type="radio" value="new">
                <span>新增一级部门</span>
              </label>
            </div>

            <label v-if="primaryMode === 'existing'" class="form-group">
              <span>已有一级部门</span>
              <select v-model="selectedPrimaryId">
                <option value="" disabled>请选择一级部门</option>
                <option v-for="primary in primaryItems" :key="primary.id" :value="primary.id">
                  {{ primary.name }}
                </option>
              </select>
            </label>

            <label v-else class="form-group">
              <span>新增一级部门</span>
              <input v-model="primaryName" type="text" placeholder="请输入一级部门名称">
            </label>
          </div>

          <div class="form-section">
            <span class="form-section-title">二级部门</span>

            <div v-if="needsTertiary" class="source-options" role="radiogroup" aria-label="二级部门来源">
              <label
                class="source-radio"
                :class="{ active: secondaryMode === 'existing', disabled: !canSelectSecondary }"
              >
                <input
                  v-model="secondaryMode"
                  type="radio"
                  value="existing"
                  :disabled="!canSelectSecondary"
                >
                <span>已有二级部门</span>
              </label>
              <label class="source-radio" :class="{ active: secondaryMode === 'new' }">
                <input v-model="secondaryMode" type="radio" value="new">
                <span>新增二级部门</span>
              </label>
            </div>

            <label v-if="needsTertiary && secondaryMode === 'existing'" class="form-group">
              <span>已有二级部门</span>
              <select v-model="selectedSecondaryId">
                <option value="" disabled>请选择二级部门</option>
                <option v-for="secondary in secondaryItems" :key="secondary.id" :value="secondary.id">
                  {{ secondary.name }}
                </option>
              </select>
            </label>

            <label v-else class="form-group">
              <span>新增二级部门</span>
              <input v-model="secondaryName" type="text" placeholder="请输入二级部门名称">
            </label>
          </div>

          <label v-if="needsTertiary" class="form-group">
            <span>三级部门名称</span>
            <input v-model="tertiaryName" type="text" placeholder="请输入三级部门名称">
          </label>
        </template>
      </div>

      <div class="modal-footer">
        <button class="btn-secondary" type="button" :disabled="submitting" @click="requestClose">取消</button>
        <button class="btn-primary" type="button" :disabled="!canSubmit" @click="submit">
          {{ submitting ? '创建中...' : '创建' }}
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
  width: min(720px, calc(100vw - 32px));
  max-height: calc(100vh - 48px);
  overflow: auto;
  background: white;
  border-radius: 12px;
  box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.1);
}

.modal-header,
.modal-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20px 24px;
  border-bottom: 1px solid #e5e7eb;
}

.modal-header h3 {
  margin: 0;
  color: #1f2937;
  font-size: 18px;
}

.modal-footer {
  border-top: 1px solid #e5e7eb;
  border-bottom: 0;
  justify-content: flex-end;
  gap: 12px;
}

.modal-body {
  display: grid;
  gap: 16px;
  padding: 24px;
}

.alert {
  padding: 12px 16px;
  border-radius: 8px;
}

.alert-error {
  background: #fef2f2;
  color: #dc2626;
}

.form-section,
.form-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.form-section {
  gap: 12px;
}

.form-group span,
.form-section-title {
  color: #374151;
  font-size: 14px;
  font-weight: 500;
}

.form-group input,
.form-group select {
  width: 100%;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  color: #1f2937;
  font: inherit;
  padding: 10px 12px;
}

.form-group input:focus,
.form-group select:focus {
  border-color: #667eea;
  box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.12);
  outline: none;
}

.segmented-control,
.source-options {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.segment-option,
.source-radio {
  border: 1px solid #d1d5db;
  border-radius: 8px;
  cursor: pointer;
  color: #374151;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 40px;
  padding: 8px 12px;
}

.segment-option input,
.source-radio input {
  margin: 0;
}

.segment-option.active,
.source-radio.active {
  border-color: #667eea;
  background: #eef2ff;
  color: #3730a3;
}

.source-radio.disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.close-btn {
  border: 0;
  background: transparent;
  color: #6b7280;
  cursor: pointer;
  font-size: 18px;
}

.close-btn:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.btn-primary,
.btn-secondary {
  border: 1px solid #d1d5db;
  border-radius: 8px;
  cursor: pointer;
  padding: 9px 14px;
}

.btn-primary {
  background: #667eea;
  border-color: #667eea;
  color: white;
}

.btn-secondary {
  background: white;
  color: #1f2937;
}

.btn-primary:disabled,
.btn-secondary:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

@media (max-width: 640px) {
  .modal-overlay {
    align-items: stretch;
    padding: 16px;
  }

  .modal {
    max-height: 100%;
  }

  .modal-header,
  .modal-footer {
    padding: 16px;
  }

  .modal-body {
    padding: 16px;
  }
}
</style>
