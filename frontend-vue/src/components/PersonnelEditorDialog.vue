<script setup>
import { computed, reactive, watch } from 'vue'
import DepartmentSelector from './DepartmentSelector.vue'

const props = defineProps({
  show: Boolean,
  mode: {
    type: String,
    default: 'create',
  },
  departmentTree: {
    type: Array,
    default: () => [],
  },
  departmentOptionsLoading: {
    type: Boolean,
    default: false,
  },
  initialValue: {
    type: Object,
    default: () => ({}),
  },
  submitting: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['close', 'submit'])

const form = reactive({
  employee_no: '',
  full_name: '',
  verification_code: '',
  status: 'active',
  remarks: '',
  primary_department_id: null,
  secondary_department_id: null,
  tertiary_department_id: null,
})

const isEditMode = computed(() => props.mode === 'edit')
const dialogTitle = computed(() => (isEditMode.value ? '编辑人员' : '新增人员'))
const verificationCodeLabel = computed(() => (isEditMode.value ? '重置校验码（可选）' : '校验码'))
const searchPlaceholder = '搜索部门名称'

function resetForm() {
  form.employee_no = ''
  form.full_name = ''
  form.verification_code = ''
  form.status = 'active'
  form.remarks = ''
  form.primary_department_id = null
  form.secondary_department_id = null
  form.tertiary_department_id = null
}

function applyInitialValue() {
  const value = props.initialValue || {}
  form.employee_no = value.employee_no || ''
  form.full_name = value.full_name || ''
  form.verification_code = ''
  form.status = value.personnel_record_status || 'active'
  form.remarks = value.remarks || ''
  form.primary_department_id = value.primary_department_id ?? null
  form.secondary_department_id = value.secondary_department_id ?? null
  form.tertiary_department_id = value.tertiary_department_id ?? null
}

watch(
  () => props.show,
  (visible) => {
    if (!visible) {
      resetForm()
      return
    }
    applyInitialValue()
  },
  { immediate: true },
)

watch(
  () => props.initialValue,
  () => {
    if (props.show) {
      applyInitialValue()
    }
  },
  { deep: true },
)

function close() {
  emit('close')
}

function submit() {
  emit('submit', {
    employee_no: form.employee_no.trim(),
    full_name: form.full_name.trim(),
    verification_code: form.verification_code.trim(),
    status: form.status,
    remarks: form.remarks.trim(),
    primary_department_id: form.primary_department_id,
    secondary_department_id: form.secondary_department_id,
    tertiary_department_id: form.tertiary_department_id,
  })
}
</script>

<template>
  <div v-if="show" class="modal-overlay" @click.self="close">
    <div class="modal modal-wide">
      <div class="modal-header">
        <h3>{{ dialogTitle }}</h3>
        <button class="close-btn" type="button" @click="close">x</button>
      </div>

      <div class="modal-body">
        <div class="form-grid">
          <label class="form-group">
            <span>工号</span>
            <input
              v-model="form.employee_no"
              type="text"
              placeholder="请输入工号"
              :readonly="isEditMode"
            >
          </label>

          <label class="form-group">
            <span>姓名</span>
            <input v-model="form.full_name" type="text" placeholder="请输入姓名">
          </label>

          <label class="form-group">
            <span>{{ verificationCodeLabel }}</span>
            <input
              v-model="form.verification_code"
              type="text"
              :placeholder="isEditMode ? '留空则不修改校验码' : '请输入校验码'"
            >
          </label>

          <label class="form-group">
            <span>状态</span>
            <select v-model="form.status">
              <option value="active">启用</option>
              <option value="disabled">停用</option>
            </select>
          </label>
        </div>

        <label class="form-group form-group-full">
          <span>备注</span>
          <textarea v-model="form.remarks" rows="3" placeholder="可选备注" />
        </label>

        <div class="form-group form-group-full">
          <span>部门信息</span>
          <DepartmentSelector
            :tree="departmentTree"
            :primary-id="form.primary_department_id"
            :secondary-id="form.secondary_department_id"
            :tertiary-id="form.tertiary_department_id"
            :disabled="departmentOptionsLoading"
            :search-placeholder="searchPlaceholder"
            @update:primary-id="form.primary_department_id = $event"
            @update:secondary-id="form.secondary_department_id = $event"
            @update:tertiary-id="form.tertiary_department_id = $event"
          />
        </div>
      </div>

      <div class="modal-footer">
        <button class="btn-secondary" type="button" :disabled="submitting" @click="close">取消</button>
        <button class="btn-primary" type="button" :disabled="submitting" @click="submit">
          {{ submitting ? '提交中...' : '保存' }}
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
  width: min(960px, calc(100vw - 32px));
  max-height: calc(100vh - 48px);
  overflow: auto;
  background: #fff;
  border-radius: 16px;
}

.modal-header,
.modal-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20px 24px;
  border-bottom: 1px solid #e5e7eb;
}

.modal-footer {
  border-top: 1px solid #e5e7eb;
  border-bottom: 0;
  justify-content: flex-end;
  gap: 12px;
}

.modal-body {
  padding: 24px;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 16px;
}

.form-group-full {
  width: 100%;
}

.form-group input,
.form-group select,
.form-group textarea {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid #d1d5db;
  border-radius: 10px;
  font: inherit;
}

.close-btn {
  border: 0;
  background: transparent;
  cursor: pointer;
  font-size: 18px;
}

@media (max-width: 768px) {
  .form-grid {
    grid-template-columns: 1fr;
  }
}
</style>
