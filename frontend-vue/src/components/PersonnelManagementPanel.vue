<script setup>
import { computed, onMounted, ref } from 'vue'
import { adminApi } from '../services/admin'
import DepartmentSelector from './DepartmentSelector.vue'
import ForceDeleteConfirmDialog from './ForceDeleteConfirmDialog.vue'
import ImportResultDialog from './ImportResultDialog.vue'
import PersonnelBatchImportDialog from './PersonnelBatchImportDialog.vue'
import PersonnelEditorDialog from './PersonnelEditorDialog.vue'
import PersonnelImportResultDialog from './PersonnelImportResultDialog.vue'

const emit = defineEmits(['updated'])

const loading = ref(false)
const error = ref('')
const success = ref('')
const personnelItems = ref([])
const selectedPersonnelIds = ref([])
const showBatchOperationResultDialog = ref(false)
const batchOperationResultTitle = ref('批量操作结果')
const batchOperationResult = ref(null)
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
const showBatchDepartmentDialog = ref(false)
const batchDepartmentSubmitting = ref(false)
const batchDepartmentForm = ref({
  primary_department_id: null,
  secondary_department_id: null,
  tertiary_department_id: null,
})
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

function openBatchOperationResult(title, resultData) {
  batchOperationResultTitle.value = title
  batchOperationResult.value = resultData
  showBatchOperationResultDialog.value = true
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
  if (!hasSelectedPersonnel.value) {
    error.value = '请至少选择一个人员'
    return
  }
  if (!window.confirm(`确定批量删除选中的 ${selectedPersonnelCount.value} 个人员吗？有绑定账号的人员会失败，其他人员继续删除。`)) {
    return
  }
  const result = await adminApi.batchDeletePersonnel(selectedPersonnelIds.value)
  if (result.success) {
    openBatchForceDeletePersonnel(result.data?.details)
    if (forceDeletePersonnelState.value.visible) {
      await fetchPersonnel()
      emit('updated')
      return
    }
    openBatchOperationResult('批量删除人员结果', result.data)
    setSuccess(formatBatchDeleteSummary(result.data))
    clearSelectedPersonnel()
    await fetchPersonnel()
    emit('updated')
    return
  }
  error.value = result.error || '批量删除人员失败'
}

async function handleBatchUpdatePersonnelStatus(status) {
  error.value = ''
  if (!hasSelectedPersonnel.value) {
    error.value = '请至少选择一个人员'
    return
  }
  const actionText = status === 'disabled' ? '停用' : '启用'
  if (!window.confirm(`确定批量${actionText}选中的 ${selectedPersonnelCount.value} 个人员吗？已是目标状态的人员会显示跳过。`)) {
    return
  }
  const result = await adminApi.batchUpdatePersonnelStatus(selectedPersonnelIds.value, status)
  if (result.success) {
    openBatchOperationResult(`批量${actionText}人员结果`, result.data)
    setSuccess(`批量${actionText}人员完成`)
    clearSelectedPersonnel()
    await fetchPersonnel()
    emit('updated')
    return
  }
  error.value = result.error || `批量${actionText}人员失败`
}

function resetBatchDepartmentForm() {
  batchDepartmentForm.value = {
    primary_department_id: null,
    secondary_department_id: null,
    tertiary_department_id: null,
  }
}

function openBatchDepartmentDialog() {
  error.value = ''
  if (!hasSelectedPersonnel.value) {
    error.value = '请至少选择一个人员'
    return
  }
  resetBatchDepartmentForm()
  showBatchDepartmentDialog.value = true
}

async function submitBatchDepartmentUpdate() {
  error.value = ''
  if (!hasSelectedPersonnel.value) {
    showBatchDepartmentDialog.value = false
    error.value = '请至少选择一个人员'
    return
  }
  if (!batchDepartmentForm.value.primary_department_id) {
    error.value = '请选择一级部门'
    return
  }
  batchDepartmentSubmitting.value = true
  const result = await adminApi.batchUpdatePersonnelDepartment(selectedPersonnelIds.value, batchDepartmentForm.value)
  batchDepartmentSubmitting.value = false
  if (result.success) {
    showBatchDepartmentDialog.value = false
    openBatchOperationResult('批量修改人员部门结果', result.data)
    setSuccess('批量修改人员部门完成')
    clearSelectedPersonnel()
    await fetchPersonnel()
    emit('updated')
    return
  }
  error.value = result.error || '批量修改人员部门失败'
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
      openBatchOperationResult('批量强制删除人员结果', result.data)
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
        <button class="btn-secondary" :disabled="!hasSelectedPersonnel" @click="handleBatchUpdatePersonnelStatus('active')">批量启用</button>
        <button class="btn-secondary" :disabled="!hasSelectedPersonnel" @click="handleBatchUpdatePersonnelStatus('disabled')">批量停用</button>
        <button class="btn-secondary" :disabled="!hasSelectedPersonnel" @click="openBatchDepartmentDialog">批量修改部门</button>
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
                <button class="action-btn action-btn-danger-soft" @click="handleDeletePersonnel(item)">删除</button>
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
    <ForceDeleteConfirmDialog
      :show="forceDeletePersonnelState.visible"
      title="强制删除人员"
      :impact-text="forceDeletePersonnelState.impactText"
      warning-text="强制删除只解绑账号，不停用账号、不删除账号。"
      :password="forceDeletePersonnelState.adminPassword"
      :submitting="forceDeletePersonnelState.submitting"
      :error="forceDeletePersonnelState.error"
      @update:password="forceDeletePersonnelState.adminPassword = $event"
      @cancel="resetForceDeletePersonnelState"
      @confirm="submitForceDeletePersonnel"
    />
    <div v-if="showBatchDepartmentDialog" class="modal-overlay" @click.self="showBatchDepartmentDialog = false">
      <div class="batch-department-modal">
        <div class="modal-header">
          <h3>批量修改部门</h3>
          <button class="close-btn" type="button" @click="showBatchDepartmentDialog = false">x</button>
        </div>
        <div class="modal-body">
          <p class="modal-hint">
            将选中的 {{ selectedPersonnelCount }} 个人员统一调整到所选部门。一级部门必选，二级和三级可按实际管理层级留空。
          </p>
          <DepartmentSelector
            :tree="selectableDepartmentTree"
            :primary-id="batchDepartmentForm.primary_department_id"
            :secondary-id="batchDepartmentForm.secondary_department_id"
            :tertiary-id="batchDepartmentForm.tertiary_department_id"
            :disabled="batchDepartmentSubmitting || departmentOptionsLoading"
            search-placeholder="搜索目标部门"
            @update:primary-id="batchDepartmentForm.primary_department_id = $event"
            @update:secondary-id="batchDepartmentForm.secondary_department_id = $event"
            @update:tertiary-id="batchDepartmentForm.tertiary_department_id = $event"
          />
        </div>
        <div class="modal-footer">
          <button class="btn-secondary" :disabled="batchDepartmentSubmitting" @click="showBatchDepartmentDialog = false">取消</button>
          <button class="btn-primary" :disabled="batchDepartmentSubmitting" @click="submitBatchDepartmentUpdate">
            {{ batchDepartmentSubmitting ? '提交中...' : '确认修改' }}
          </button>
        </div>
      </div>
    </div>
    <ImportResultDialog
      :show="showBatchOperationResultDialog"
      :result="batchOperationResult"
      :title="batchOperationResultTitle"
      @close="showBatchOperationResultDialog = false"
    />
  </section>
</template>

<style scoped>
.personnel-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.modal-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(17, 24, 39, 0.45);
}

.batch-department-modal {
  width: min(720px, calc(100vw - 32px));
  max-height: 90vh;
  overflow-y: auto;
  border-radius: 8px;
  background: white;
  box-shadow: 0 20px 30px rgba(15, 23, 42, 0.18);
}

.modal-header,
.modal-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 16px 20px;
}

.modal-header {
  border-bottom: 1px solid #e5e7eb;
}

.modal-header h3 {
  margin: 0;
  color: #111827;
  font-size: 18px;
}

.modal-body {
  display: grid;
  gap: 16px;
  padding: 20px;
}

.modal-hint {
  margin: 0;
  color: #4b5563;
  font-size: 14px;
}

.modal-footer {
  justify-content: flex-end;
  border-top: 1px solid #e5e7eb;
}

.close-btn {
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: #6b7280;
  cursor: pointer;
  font-size: 20px;
}

.close-btn:hover {
  background: #f3f4f6;
  color: #111827;
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

.action-btn-danger-soft {
  border-color: #fecaca;
  color: #b91c1c;
  background: #fffaf9;
}

.action-btn-danger-soft:hover {
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
