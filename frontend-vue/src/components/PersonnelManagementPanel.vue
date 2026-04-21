<script setup>
import { onMounted, ref } from 'vue'
import { adminApi } from '../services/admin'
import PersonnelBatchImportDialog from './PersonnelBatchImportDialog.vue'
import PersonnelEditorDialog from './PersonnelEditorDialog.vue'
import PersonnelImportResultDialog from './PersonnelImportResultDialog.vue'

const emit = defineEmits(['updated'])

const loading = ref(false)
const error = ref('')
const success = ref('')
const personnelItems = ref([])
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

function setSuccess(message) {
  success.value = message
  error.value = ''
  setTimeout(() => {
    success.value = ''
  }, 3000)
}

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
    error.value = ''
  } else {
    personnelItems.value = []
    error.value = result.error || '获取人员列表失败'
  }
  loading.value = false
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
                <button class="expand-btn" @click="toggleBindings(item.id)">
                  {{ isBindingsExpanded(item.id) ? '收起' : '展开' }}
                </button>
              </td>
              <td>{{ item.employee_no }}</td>
              <td>{{ item.full_name }}</td>
              <td>{{ item.department_display || '-' }}</td>
              <td>{{ item.personnel_record_status }}</td>
              <td>{{ item.binding_count }}</td>
              <td>{{ item.updated_at || '-' }}</td>
              <td class="row-actions">
                <button class="link-btn" @click="openEditDialog(item)">编辑</button>
                <button class="link-btn" @click="handleTogglePersonnelStatus(item)">
                  {{ item.personnel_record_status === 'active' ? '停用' : '启用' }}
                </button>
                <button class="link-btn" @click="toggleBindings(item.id)">查看绑定</button>
              </td>
            </tr>
            <tr v-if="isBindingsExpanded(item.id)" class="bindings-row">
              <td colspan="8">
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

.panel-actions,
.filter-actions {
  align-items: center;
}

.filter-grid {
  flex-wrap: wrap;
}

.filter-grid label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 180px;
}

.filters-card,
.table-card {
  background: white;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  padding: 16px;
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

.bindings-state.error {
  color: #b91c1c;
}
</style>
