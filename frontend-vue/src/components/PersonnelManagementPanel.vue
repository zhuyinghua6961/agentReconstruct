<script setup>
import { computed, onMounted, ref } from 'vue'
import { adminApi } from '../services/admin'
import PersonnelBatchImportDialog from './PersonnelBatchImportDialog.vue'
import PersonnelEditorDialog from './PersonnelEditorDialog.vue'
import PersonnelImportResultDialog from './PersonnelImportResultDialog.vue'

const emit = defineEmits(['updated'])

const loading = ref(false)
const error = ref('')
const success = ref('')
const personnelItems = ref([])
const selectedPersonnelIds = ref([])
const batchDeleteResult = ref(null)
const searchEmployeeNo = ref('')
const searchFullName = ref('')
const statusFilter = ref('')
const expandedPersonnelIds = ref([])
const bindingsByPersonnelId = ref({})
const bindingsLoadingByPersonnelId = ref({})
const bindingsErrorByPersonnelId = ref({})
const showPersonnelImportDialog = ref(false)
const showPersonnelImportResultDialog = ref(false)
const personnelImportResult = ref(null)
const showPersonnelEditorDialog = ref(false)
const personnelEditorMode = ref('create')
const selectedPersonnel = ref(null)
const personnelSubmitting = ref(false)
const selectableDepartmentTree = ref([])
const departmentOptionsLoading = ref(false)
const forceDeletePersonnelState = ref({
  visible: false,
  mode: 'single',
  item: null,
  ids: [],
  adminPassword: '',
  submitting: false,
  error: '',
  impactText: '',
})

function setSuccess(message) {
  success.value = message
  error.value = ''
  setTimeout(() => {
    success.value = ''
  }, 3000)
}

const selectedPersonnelCount = computed(() => selectedPersonnelIds.value.length)
const hasSelectedPersonnel = computed(() => selectedPersonnelCount.value > 0)
const allCurrentPersonnelSelected = computed(() => {
  const ids = personnelItems.value.map(item => Number(item.id)).filter(Boolean)
  return ids.length > 0 && ids.every(id => selectedPersonnelIds.value.includes(id))
})

async function fetchPersonnel() {
  loading.value = true
  const result = await adminApi.getPersonnel({
    employee_no: searchEmployeeNo.value,
    full_name: searchFullName.value,
    status: statusFilter.value,
    page_size: 100,
  })
  if (result.success) {
    personnelItems.value = Array.isArray(result.data?.items) ? result.data.items : []
    const currentIds = new Set(personnelItems.value.map(item => Number(item.id)))
    selectedPersonnelIds.value = selectedPersonnelIds.value.filter(id => currentIds.has(Number(id)))
    error.value = ''
  } else {
    personnelItems.value = []
    error.value = result.error || '获取人员列表失败'
  }
  loading.value = false
}

function togglePersonnelSelection(personnelId) {
  const normalizedId = Number(personnelId)
  if (!normalizedId) {
    return
  }
  if (selectedPersonnelIds.value.includes(normalizedId)) {
    selectedPersonnelIds.value = selectedPersonnelIds.value.filter(id => id !== normalizedId)
    return
  }
  selectedPersonnelIds.value = [...selectedPersonnelIds.value, normalizedId]
}

function toggleSelectAllCurrentPersonnel() {
  const ids = personnelItems.value.map(item => Number(item.id)).filter(Boolean)
  if (allCurrentPersonnelSelected.value) {
    const currentIds = new Set(ids)
    selectedPersonnelIds.value = selectedPersonnelIds.value.filter(id => !currentIds.has(Number(id)))
    return
  }
  selectedPersonnelIds.value = Array.from(new Set([...selectedPersonnelIds.value, ...ids]))
}

function clearSelectedPersonnel() {
  selectedPersonnelIds.value = []
}

function formatBatchDeleteSummary(result) {
  const summary = result?.summary || {}
  return `批量删除完成：成功 ${summary.success || 0} 条，失败 ${summary.failed || 0} 条`
}

function resetForceDeletePersonnelState() {
  forceDeletePersonnelState.value = {
    visible: false,
    mode: 'single',
    item: null,
    ids: [],
    adminPassword: '',
    submitting: false,
    error: '',
    impactText: '',
  }
}

function openSingleForceDeletePersonnel(item, reason = '') {
  const bindingCount = Number(item?.binding_count || 0)
  forceDeletePersonnelState.value = {
    visible: true,
    mode: 'single',
    item,
    ids: [Number(item?.id)].filter(Boolean),
    adminPassword: '',
    submitting: false,
    error: '',
    impactText: reason || `该人员仍有 ${bindingCount} 个绑定账号。强制删除将解绑这些账号，账号下次登录需要重新绑定人员。`,
  }
}

function openBatchForceDeletePersonnel(details = []) {
  const forceItems = (Array.isArray(details) ? details : [])
    .filter(detail => detail?.code === 'PERSONNEL_HAS_BINDINGS')
  const ids = Array.from(new Set(forceItems.map(detail => Number(detail.personnel_id)).filter(Boolean)))
  if (!ids.length) {
    return
  }
  forceDeletePersonnelState.value = {
    visible: true,
    mode: 'batch',
    item: null,
    ids,
    adminPassword: '',
    submitting: false,
    error: '',
    impactText: `本次有 ${ids.length} 个人员仍有绑定账号。强制删除将解绑这些账号，账号下次登录需要重新绑定人员。`,
  }
}

async function fetchDepartmentTree() {
  departmentOptionsLoading.value = true
  const result = await adminApi.getDepartmentTree()
  if (result.success) {
    selectableDepartmentTree.value = Array.isArray(result.data?.items) ? result.data.items : []
  } else {
    error.value = result.error || '获取部门选项失败'
    selectableDepartmentTree.value = []
  }
  departmentOptionsLoading.value = false
}

function resetFilters() {
  searchEmployeeNo.value = ''
  searchFullName.value = ''
  statusFilter.value = ''
  fetchPersonnel()
}

function isBindingsExpanded(personnelId) {
  return expandedPersonnelIds.value.includes(Number(personnelId))
}

function getPersonnelStatusText(status) {
  return String(status || '').trim().toLowerCase() === 'active' ? '启用' : '停用'
}

function getPersonnelStatusClass(status) {
  return String(status || '').trim().toLowerCase() === 'active' ? 'active' : 'disabled'
}

async function loadBindings(personnelId, { force = false } = {}) {
  const normalizedId = Number(personnelId)
  if (!force && bindingsByPersonnelId.value[normalizedId]) {
    return
  }
  bindingsLoadingByPersonnelId.value = {
    ...bindingsLoadingByPersonnelId.value,
    [normalizedId]: true,
  }
  bindingsErrorByPersonnelId.value = {
    ...bindingsErrorByPersonnelId.value,
    [normalizedId]: '',
  }
  const result = await adminApi.getPersonnelBindings(normalizedId)
  if (result.success) {
    bindingsByPersonnelId.value = {
      ...bindingsByPersonnelId.value,
      [normalizedId]: Array.isArray(result.data?.items) ? result.data.items : [],
    }
  } else {
    bindingsErrorByPersonnelId.value = {
      ...bindingsErrorByPersonnelId.value,
      [normalizedId]: result.error || '获取绑定账号失败',
    }
  }
  bindingsLoadingByPersonnelId.value = {
    ...bindingsLoadingByPersonnelId.value,
    [normalizedId]: false,
  }
}

async function toggleBindings(personnelId) {
  const normalizedId = Number(personnelId)
  if (isBindingsExpanded(normalizedId)) {
    expandedPersonnelIds.value = expandedPersonnelIds.value.filter(id => id !== normalizedId)
    return
  }
  expandedPersonnelIds.value = [...expandedPersonnelIds.value, normalizedId]
  await loadBindings(normalizedId)
}

function openCreateDialog() {
  personnelEditorMode.value = 'create'
  selectedPersonnel.value = null
  showPersonnelEditorDialog.value = true
}

function openEditDialog(item) {
  selectedPersonnel.value = item
  personnelEditorMode.value = 'edit'
  showPersonnelEditorDialog.value = true
}

async function handlePersonnelEditorSubmit(payload) {
  personnelSubmitting.value = true
  const normalizedPayload = {
    employee_no: payload.employee_no,
    full_name: payload.full_name,
    verification_code: payload.verification_code || undefined,
    status: payload.status || 'active',
    remarks: payload.remarks || null,
    primary_department_id: payload.primary_department_id ?? null,
    secondary_department_id: payload.secondary_department_id ?? null,
    tertiary_department_id: payload.tertiary_department_id ?? null,
  }

  if (personnelEditorMode.value === 'create') {
    const createResult = await adminApi.createPersonnel(normalizedPayload)
    personnelSubmitting.value = false
    if (!createResult.success) {
      error.value = createResult.error || '创建人员失败'
      return
    }
    showPersonnelEditorDialog.value = false
    setSuccess('人员创建成功')
    await fetchPersonnel()
    emit('updated')
    return
  }

  const currentItem = selectedPersonnel.value
  const updateResult = await adminApi.updatePersonnel(currentItem.id, {
    full_name: normalizedPayload.full_name,
    verification_code: normalizedPayload.verification_code,
    status: normalizedPayload.status,
    remarks: normalizedPayload.remarks,
    primary_department_id: normalizedPayload.primary_department_id,
    secondary_department_id: normalizedPayload.secondary_department_id,
    tertiary_department_id: normalizedPayload.tertiary_department_id,
  })
  if (!updateResult.success) {
    personnelSubmitting.value = false
    error.value = updateResult.error || '更新人员失败'
    return
  }

  personnelSubmitting.value = false
  showPersonnelEditorDialog.value = false
  setSuccess('人员信息已更新')
  await fetchPersonnel()
  emit('updated')
}

async function handleTogglePersonnelStatus(item) {
  const targetStatus = item.personnel_record_status === 'active' ? 'disabled' : 'active'
  const actionText = targetStatus === 'disabled' ? '停用' : '启用'
  if (!window.confirm(`确定要${actionText}人员 ${item.employee_no} / ${item.full_name} 吗？`)) {
    return
  }
  const result = await adminApi.updatePersonnelStatus(item.id, targetStatus)
  if (result.success) {
    setSuccess(`人员已${actionText}`)
    await fetchPersonnel()
    emit('updated')
    return
  }
  error.value = result.error || `人员${actionText}失败`
}

async function handleDeletePersonnel(item) {
  const bindingCount = Number(item.binding_count || 0)
  if (bindingCount > 0) {
    error.value = '该人员仍有绑定账号，可输入管理员密码强制删除'
    openSingleForceDeletePersonnel(item)
    return
  }
  if (!window.confirm(`确定要删除人员 ${item.employee_no} / ${item.full_name} 吗？此操作不可恢复。`)) {
    return
  }
  const result = await adminApi.deletePersonnel(item.id)
  if (result.success) {
    setSuccess('人员已删除')
    expandedPersonnelIds.value = expandedPersonnelIds.value.filter(id => id !== Number(item.id))
    await fetchPersonnel()
    emit('updated')
    return
  }
  if (result.code === 'PERSONNEL_HAS_BINDINGS') {
    openSingleForceDeletePersonnel(item, result.error)
    return
  }
  error.value = result.error || '删除人员失败'
}

async function handleBatchDeletePersonnel() {
  error.value = ''
  batchDeleteResult.value = null
  if (!hasSelectedPersonnel.value) {
    error.value = '请至少选择一个人员'
    return
  }
  if (!window.confirm(`确定批量删除选中的 ${selectedPersonnelCount.value} 个人员吗？有绑定账号的人员会失败，其他人员继续删除。`)) {
    return
  }
  const result = await adminApi.batchDeletePersonnel(selectedPersonnelIds.value)
  if (result.success) {
    batchDeleteResult.value = result.data
    setSuccess(formatBatchDeleteSummary(result.data))
    openBatchForceDeletePersonnel(result.data?.details)
    if (!forceDeletePersonnelState.value.visible) {
      clearSelectedPersonnel()
    }
    await fetchPersonnel()
    emit('updated')
    return
  }
  error.value = result.error || '批量删除人员失败'
}

async function submitForceDeletePersonnel() {
  const state = forceDeletePersonnelState.value
  const adminPassword = String(state.adminPassword || '').trim()
  if (!adminPassword) {
    forceDeletePersonnelState.value = { ...state, error: '请输入管理员密码' }
    return
  }
  forceDeletePersonnelState.value = { ...state, submitting: true, error: '' }
  const result = state.mode === 'batch'
    ? await adminApi.batchForceDeletePersonnel(state.ids, adminPassword)
    : await adminApi.forceDeletePersonnel(state.ids[0], adminPassword)
  if (result.success) {
    if (state.mode === 'batch') {
      batchDeleteResult.value = result.data
    }
    setSuccess(result.message || '强制删除完成')
    expandedPersonnelIds.value = expandedPersonnelIds.value.filter(id => !state.ids.includes(Number(id)))
    selectedPersonnelIds.value = selectedPersonnelIds.value.filter(id => !state.ids.includes(Number(id)))
    resetForceDeletePersonnelState()
    await fetchPersonnel()
    emit('updated')
    return
  }
  forceDeletePersonnelState.value = {
    ...state,
    submitting: false,
    error: result.error || '强制删除人员失败',
  }
}

async function downloadPersonnelImportTemplate(format = 'xlsx') {
  try {
    await adminApi.downloadPersonnelImportTemplate(format)
  } catch (err) {
    error.value = err?.message || '下载模板失败'
  }
}

async function handlePersonnelImportSuccess(result) {
  personnelImportResult.value = result
  showPersonnelImportDialog.value = false
  showPersonnelImportResultDialog.value = true
  await fetchPersonnel()
  emit('updated')
}

onMounted(() => {
  fetchDepartmentTree()
  fetchPersonnel()
})
</script>

<template>
  <section class="personnel-panel">
    <div class="panel-header">
      <div>
        <h3>人员表</h3>
        <p class="panel-hint">维护工号、姓名、状态、部门和绑定账号关系。</p>
      </div>
      <div class="panel-actions">
        <div v-if="hasSelectedPersonnel" class="selection-summary" aria-live="polite">
          <span>已选择</span>
          <strong>{{ selectedPersonnelCount }}</strong>
          <span>个人员</span>
        </div>
        <button class="btn-danger" :disabled="!hasSelectedPersonnel" @click="handleBatchDeletePersonnel">批量删除</button>
        <button class="btn-secondary" :disabled="!hasSelectedPersonnel" @click="clearSelectedPersonnel">清空选择</button>
        <button class="btn-secondary" @click="downloadPersonnelImportTemplate('xlsx')">下载模板</button>
        <button class="btn-secondary" @click="showPersonnelImportDialog = true">批量导入</button>
        <button class="btn-primary" @click="openCreateDialog">新增人员</button>
      </div>
    </div>

    <div class="filters-card">
      <div class="filter-grid">
        <label>
          <span>工号</span>
          <input v-model="searchEmployeeNo" type="text" placeholder="按工号搜索">
        </label>
        <label>
          <span>姓名</span>
          <input v-model="searchFullName" type="text" placeholder="按姓名搜索">
        </label>
        <label>
          <span>状态</span>
          <select v-model="statusFilter">
            <option value="">全部状态</option>
            <option value="active">启用</option>
            <option value="disabled">停用</option>
          </select>
        </label>
      </div>
      <div class="filter-actions">
        <button class="btn-secondary" @click="resetFilters">重置</button>
        <button class="btn-primary" @click="fetchPersonnel">搜索</button>
      </div>
    </div>

    <div v-if="success" class="alert alert-success">{{ success }}</div>
    <div v-if="error" class="alert alert-error">{{ error }}</div>
    <div v-if="forceDeletePersonnelState.visible" class="force-delete-card">
      <div>
        <h4>强制删除确认</h4>
        <p>{{ forceDeletePersonnelState.impactText }}</p>
        <p class="force-delete-warning">强制删除只解绑账号，不停用账号、不删除账号。</p>
      </div>
      <label>
        <span>管理员密码</span>
        <input
          v-model="forceDeletePersonnelState.adminPassword"
          type="password"
          autocomplete="current-password"
          placeholder="输入当前管理员密码"
        >
      </label>
      <div v-if="forceDeletePersonnelState.error" class="force-delete-error">
        {{ forceDeletePersonnelState.error }}
      </div>
      <div class="force-delete-actions">
        <button class="btn-secondary" :disabled="forceDeletePersonnelState.submitting" @click="resetForceDeletePersonnelState">
          取消
        </button>
        <button class="btn-danger" :disabled="forceDeletePersonnelState.submitting" @click="submitForceDeletePersonnel">
          {{ forceDeletePersonnelState.submitting ? '删除中...' : '确认强制删除' }}
        </button>
      </div>
    </div>
    <div v-if="batchDeleteResult?.details?.length" class="batch-result-card">
      <div class="batch-result-title">{{ formatBatchDeleteSummary(batchDeleteResult) }}</div>
      <ul class="batch-result-list">
        <li
          v-for="detail in batchDeleteResult.details"
          :key="`${detail.personnel_id}-${detail.row}`"
          :class="detail.status"
        >
          {{ detail.employee_no || detail.personnel_id }} / {{ detail.full_name || '-' }}：{{ detail.message }}
        </li>
      </ul>
    </div>

    <div class="table-card">
      <div v-if="loading" class="loading-state">加载中...</div>
      <div v-else-if="!personnelItems.length" class="empty-state">暂无人员数据</div>
      <table v-else class="personnel-table">
        <thead>
          <tr>
            <th></th>
            <th class="checkbox-col">
              <input
                type="checkbox"
                :checked="allCurrentPersonnelSelected"
                @change="toggleSelectAllCurrentPersonnel"
              >
            </th>
            <th>工号</th>
            <th>姓名</th>
            <th>部门</th>
            <th>状态</th>
            <th>绑定账号数</th>
            <th>更新时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="item in personnelItems" :key="item.id">
            <tr>
              <td>
                <button class="action-btn expand-btn" @click="toggleBindings(item.id)">
                  {{ isBindingsExpanded(item.id) ? '收起' : '展开' }}
                </button>
              </td>
              <td class="checkbox-col">
                <input
                  type="checkbox"
                  :checked="selectedPersonnelIds.includes(Number(item.id))"
                  @change="togglePersonnelSelection(item.id)"
                >
              </td>
              <td>{{ item.employee_no }}</td>
              <td>{{ item.full_name }}</td>
              <td>{{ item.department_display || '-' }}</td>
              <td>
                <span class="status-badge" :class="getPersonnelStatusClass(item.personnel_record_status)">
                  {{ getPersonnelStatusText(item.personnel_record_status) }}
                </span>
              </td>
              <td>{{ item.binding_count }}</td>
              <td>{{ item.updated_at || '-' }}</td>
              <td class="row-actions">
                <button class="action-btn" @click="openEditDialog(item)">编辑</button>
                <button class="action-btn" @click="handleTogglePersonnelStatus(item)">
                  {{ item.personnel_record_status === 'active' ? '停用' : '启用' }}
                </button>
                <button class="action-btn" @click="toggleBindings(item.id)">查看绑定</button>
                <button class="action-btn btn-danger" @click="handleDeletePersonnel(item)">删除</button>
              </td>
            </tr>
            <tr v-if="isBindingsExpanded(item.id)" class="bindings-row">
              <td colspan="9">
                <div v-if="bindingsLoadingByPersonnelId[item.id]" class="bindings-state">加载绑定账号中...</div>
                <div v-else-if="bindingsErrorByPersonnelId[item.id]" class="bindings-state error">
                  {{ bindingsErrorByPersonnelId[item.id] }}
                </div>
                <div v-else-if="!(bindingsByPersonnelId[item.id] || []).length" class="bindings-state">暂无绑定账号</div>
                <ul v-else class="bindings-list">
                  <li v-for="binding in bindingsByPersonnelId[item.id]" :key="binding.id">
                    {{ binding.username }} / {{ binding.role }} / {{ binding.status }}
                  </li>
                </ul>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>

    <PersonnelBatchImportDialog
      :show="showPersonnelImportDialog"
      @close="showPersonnelImportDialog = false"
      @import-success="handlePersonnelImportSuccess"
    />
    <PersonnelEditorDialog
      :show="showPersonnelEditorDialog"
      :mode="personnelEditorMode"
      :initial-value="selectedPersonnel || {}"
      :department-tree="selectableDepartmentTree"
      :department-options-loading="departmentOptionsLoading"
      :submitting="personnelSubmitting"
      @close="showPersonnelEditorDialog = false"
      @submit="handlePersonnelEditorSubmit"
    />
    <PersonnelImportResultDialog
      :show="showPersonnelImportResultDialog"
      :result="personnelImportResult"
      @close="showPersonnelImportResultDialog = false"
    />
  </section>
</template>

<style scoped>
.personnel-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.panel-header,
.panel-actions,
.filter-grid,
.filter-actions,
.row-actions {
  display: flex;
  gap: 12px;
}

.panel-header {
  justify-content: space-between;
  align-items: flex-start;
}

.panel-header h3 {
  margin: 0;
  color: #1f2937;
  font-size: 18px;
}

.panel-hint {
  margin: 6px 0 0;
  color: #6b7280;
  font-size: 14px;
}

.panel-actions,
.filter-actions {
  align-items: center;
  flex-wrap: wrap;
}

.selection-summary {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  border: 1px solid #bfdbfe;
  border-radius: 999px;
  background: #eff6ff;
  color: #1d4ed8;
  font-size: 13px;
  padding: 8px 12px;
}

.selection-summary strong {
  color: #1d4ed8;
  font-size: 18px;
  line-height: 1;
}

.filter-grid {
  flex-wrap: wrap;
}

.filters-card {
  background: #f9fafb;
}

.filter-grid label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 180px;
  color: #374151;
  font-size: 14px;
}

.filter-grid input,
.filter-grid select {
  border: 1px solid #d1d5db;
  border-radius: 6px;
  color: #1f2937;
  font-size: 14px;
  padding: 9px 12px;
}

.filter-grid input:focus,
.filter-grid select:focus {
  border-color: #667eea;
  box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.12);
  outline: none;
}

.filters-card,
.table-card {
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  padding: 16px;
}

.table-card {
  background: white;
  border: none;
  border-radius: 0;
  padding: 0;
}

.personnel-table {
  width: 100%;
  border-collapse: collapse;
}

.personnel-table th,
.personnel-table td {
  padding: 12px;
  border-bottom: 1px solid #e5e7eb;
  text-align: left;
}

.personnel-table th {
  background: #f9fafb;
  color: #374151;
  font-size: 14px;
  font-weight: 500;
}

.personnel-table td {
  color: #1f2937;
  font-size: 14px;
}

.status-badge {
  border-radius: 4px;
  font-size: 12px;
  padding: 4px 8px;
}

.status-badge.active {
  background: #dcfce7;
  color: #166534;
}

.status-badge.disabled {
  background: #fee2e2;
  color: #dc2626;
}

.action-btn {
  background: white;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  color: #374151;
  cursor: pointer;
  font-size: 12px;
  padding: 6px 12px;
}

.action-btn:hover {
  background: #f9fafb;
}

.action-btn.btn-danger {
  border-color: #fecaca;
  color: #b91c1c;
}

.action-btn.btn-danger:hover {
  background: #fef2f2;
}

.btn-primary,
.btn-secondary,
.btn-danger {
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-size: 14px;
  padding: 10px 20px;
}

.btn-primary {
  background: #667eea;
  color: white;
}

.btn-primary:hover {
  background: #5a67d8;
}

.btn-secondary {
  background: #f3f4f6;
  border: 1px solid #d1d5db;
  color: #374151;
}

.btn-secondary:hover {
  background: #e5e7eb;
}

.btn-danger {
  background: #dc2626;
  color: white;
}

.btn-danger:hover:not(:disabled) {
  background: #b91c1c;
}

.btn-danger:disabled,
.btn-secondary:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.batch-result-card {
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  background: #fafafa;
  padding: 14px 16px;
}

.force-delete-card {
  display: grid;
  gap: 12px;
  border: 1px solid #fecaca;
  border-radius: 8px;
  background: #fff7f7;
  padding: 16px;
}

.force-delete-card h4 {
  color: #991b1b;
  font-size: 15px;
  margin: 0 0 6px;
}

.force-delete-card p {
  color: #374151;
  font-size: 14px;
  margin: 0;
}

.force-delete-warning,
.force-delete-error {
  color: #b91c1c;
  font-size: 13px;
}

.force-delete-card label {
  display: grid;
  gap: 6px;
  max-width: 360px;
}

.force-delete-card input {
  border: 1px solid #d1d5db;
  border-radius: 6px;
  padding: 10px 12px;
}

.force-delete-actions {
  display: flex;
  gap: 10px;
  justify-content: flex-end;
}

.batch-result-title {
  color: #1f2937;
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 10px;
}

.batch-result-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 8px;
}

.batch-result-list li {
  border-radius: 8px;
  padding: 10px 12px;
  background: white;
  border: 1px solid #e5e7eb;
  color: #374151;
}

.batch-result-list li.success {
  border-color: #bbf7d0;
  background: #f0fdf4;
}

.batch-result-list li.failed {
  border-color: #fecaca;
  background: #fef2f2;
}

.bindings-row td {
  background: #f9fafb;
}

.bindings-list {
  margin: 0;
  padding-left: 18px;
}

.alert-error {
  color: #b91c1c;
}

.alert-success {
  color: #047857;
}

.empty-state,
.loading-state,
.bindings-state {
  color: #6b7280;
}

.empty-state,
.loading-state {
  padding: 40px;
  text-align: center;
}

.bindings-state.error {
  color: #b91c1c;
}
</style>
