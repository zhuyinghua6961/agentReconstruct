<script setup>
import { computed, onMounted, ref } from 'vue'
import { adminApi } from '../services/admin'
import DepartmentBatchImportDialog from './DepartmentBatchImportDialog.vue'
import DepartmentCreateDialog from './DepartmentCreateDialog.vue'
import DepartmentImportResultDialog from './DepartmentImportResultDialog.vue'
import { createDepartmentUsersRuntime } from '../utils/departmentSecondaryUsersRuntime'
import { buildDepartmentRenderPrimary } from '../utils/departmentManagementTreeModel'

const emit = defineEmits(['updated'])

const loading = ref(false)
const error = ref('')
const success = ref('')
const departmentTree = ref([])
const showDepartmentCreateDialog = ref(false)
const showDepartmentImportDialog = ref(false)
const showDepartmentImportResultDialog = ref(false)
const departmentImportResult = ref(null)
const expandedPrimaryIds = ref([])
const expandedSecondaryIds = ref([])
const selectedDepartmentItems = ref([])
const departmentBatchDeleteResult = ref(null)
const forceDeleteDepartmentState = ref({
  visible: false,
  mode: 'single',
  item: null,
  items: [],
  adminPassword: '',
  submitting: false,
  error: '',
  impactText: '',
})
const DIRECT_SECONDARY_NODE_LABEL = '直属二级部门成员'

const departmentUsersRuntime = createDepartmentUsersRuntime({
  requestUsers: (nodeKey) => {
    const normalizedKey = String(nodeKey)
    if (normalizedKey.startsWith('direct-primary-')) {
      const primaryId = Number(normalizedKey.replace('direct-primary-', ''))
      return adminApi.getPrimaryDirectDepartmentUsers(primaryId)
    }
    if (normalizedKey.startsWith('direct-secondary-')) {
      const secondaryId = Number(normalizedKey.replace('direct-secondary-', ''))
      return adminApi.getSecondaryDirectDepartmentUsers(secondaryId)
    }
    return adminApi.getTertiaryDepartmentUsers(Number(normalizedKey))
  },
})
const expandedDepartmentNodeIds = departmentUsersRuntime.expandedIds
const departmentUsersById = departmentUsersRuntime.usersById
const departmentUsersLoadingById = departmentUsersRuntime.loadingById
const departmentUsersErrorById = departmentUsersRuntime.errorById

const departmentRenderTree = computed(() => (
  (Array.isArray(departmentTree.value) ? departmentTree.value : []).map(primary => buildDepartmentRenderPrimary(primary))
))
const selectedDepartmentCount = computed(() => selectedDepartmentItems.value.length)
const hasSelectedDepartments = computed(() => selectedDepartmentCount.value > 0)

function setSuccess(message) {
  success.value = message
  error.value = ''
  setTimeout(() => {
    success.value = ''
  }, 3000)
}

function departmentSelectionKey(level, id) {
  return `${level}:${Number(id)}`
}

function collectSelectableDepartments(items) {
  const options = []
  ;(Array.isArray(items) ? items : []).forEach((primary) => {
    const primaryId = Number(primary?.id)
    if (primaryId) {
      options.push({
        key: departmentSelectionKey('primary', primaryId),
        level: 'primary',
        id: primaryId,
        name: primary.name || '',
      })
    }
    ;(Array.isArray(primary?.secondary_items) ? primary.secondary_items : []).forEach((secondary) => {
      const secondaryId = Number(secondary?.id)
      if (secondaryId) {
        options.push({
          key: departmentSelectionKey('secondary', secondaryId),
          level: 'secondary',
          id: secondaryId,
          name: secondary.name || '',
        })
      }
      ;(Array.isArray(secondary?.tertiary_items) ? secondary.tertiary_items : []).forEach((tertiary) => {
        const tertiaryId = Number(tertiary?.id)
        if (tertiaryId) {
          options.push({
            key: departmentSelectionKey('tertiary', tertiaryId),
            level: 'tertiary',
            id: tertiaryId,
            name: tertiary.name || '',
          })
        }
      })
    })
  })
  return options
}

function pruneSelectedDepartments() {
  const currentKeys = new Set(collectSelectableDepartments(departmentTree.value).map(item => item.key))
  selectedDepartmentItems.value = selectedDepartmentItems.value.filter(item => currentKeys.has(item.key))
}

async function fetchDepartmentTree() {
  loading.value = true
  const result = await adminApi.getDepartmentTree()
  if (result.success) {
    departmentTree.value = Array.isArray(result.data?.items) ? result.data.items : []
    pruneSelectedDepartments()
    expandedPrimaryIds.value = []
    expandedSecondaryIds.value = []
    departmentUsersRuntime.reset()
    error.value = ''
  } else {
    error.value = result.error || '获取部门列表失败'
  }
  loading.value = false
}

function isPrimaryExpanded(primaryId) {
  return expandedPrimaryIds.value.includes(Number(primaryId))
}

function togglePrimary(primaryId) {
  const normalizedId = Number(primaryId)
  if (expandedPrimaryIds.value.includes(normalizedId)) {
    expandedPrimaryIds.value = expandedPrimaryIds.value.filter(item => item !== normalizedId)
    return
  }
  expandedPrimaryIds.value = [...expandedPrimaryIds.value, normalizedId]
}

function isSecondaryExpanded(secondaryId) {
  return expandedSecondaryIds.value.includes(Number(secondaryId))
}

function toggleSecondary(secondaryId) {
  const normalizedId = Number(secondaryId)
  if (expandedSecondaryIds.value.includes(normalizedId)) {
    expandedSecondaryIds.value = expandedSecondaryIds.value.filter(item => item !== normalizedId)
    return
  }
  expandedSecondaryIds.value = [...expandedSecondaryIds.value, normalizedId]
}

function isDepartmentNodeExpanded(nodeKey) {
  return departmentUsersRuntime.isExpanded(nodeKey)
}

function primaryDirectNodeKey(primary) {
  return `direct-primary-${primary.id}`
}

function isDirectDepartmentNode(node) {
  return node?.nodeType === 'primary_direct' || node?.nodeType === 'secondary_direct'
}

function departmentNodeStatusClass(node) {
  if (isDirectDepartmentNode(node)) {
    return 'active'
  }
  return node?.effectiveStatus || 'active'
}

function departmentNodeStatusLabel(node) {
  if (isDirectDepartmentNode(node)) {
    return '直属'
  }
  return node?.effectiveStatus === 'active' ? '可选' : '已停用'
}

function departmentNodeName(node) {
  if (node?.nodeType === 'secondary_direct') {
    return DIRECT_SECONDARY_NODE_LABEL
  }
  return node?.name || ''
}

function isDepartmentSelected(level, id) {
  const key = departmentSelectionKey(level, id)
  return selectedDepartmentItems.value.some(item => item.key === key)
}

function toggleDepartmentSelection(level, id, name) {
  const normalizedId = Number(id)
  if (!normalizedId) {
    return
  }
  const key = departmentSelectionKey(level, normalizedId)
  if (selectedDepartmentItems.value.some(item => item.key === key)) {
    selectedDepartmentItems.value = selectedDepartmentItems.value.filter(item => item.key !== key)
    return
  }
  selectedDepartmentItems.value = [
    ...selectedDepartmentItems.value,
    {
      key,
      level,
      id: normalizedId,
      name: name || '',
    },
  ]
}

function clearSelectedDepartments() {
  selectedDepartmentItems.value = []
}

function formatDepartmentBatchDeleteSummary(result) {
  const summary = result?.summary || {}
  return `批量删除完成：成功 ${summary.success || 0} 条，失败 ${summary.failed || 0} 条`
}

function resetForceDeleteDepartmentState() {
  forceDeleteDepartmentState.value = {
    visible: false,
    mode: 'single',
    item: null,
    items: [],
    adminPassword: '',
    submitting: false,
    error: '',
    impactText: '',
  }
}

function openSingleForceDeleteDepartment({ level, id, name, reason = '' }) {
  forceDeleteDepartmentState.value = {
    visible: true,
    mode: 'single',
    item: { level, id: Number(id), name: name || '' },
    items: [{ level, id: Number(id) }],
    adminPassword: '',
    submitting: false,
    error: '',
    impactText: reason || `该${departmentLevelText(level)}仍有下级部门、人员或账号关联。强制删除将删除下级部门，并清空相关人员和账号部门。`,
  }
}

function openBatchForceDeleteDepartments(details = []) {
  const forceItems = (Array.isArray(details) ? details : [])
    .filter(detail => detail?.code === 'DEPARTMENT_IN_USE')
    .map(detail => ({ level: detail.level, id: Number(detail.id), name: detail.department_name || '' }))
    .filter(item => item.level && item.id)
  const seen = new Set()
  const uniqueItems = forceItems.filter((item) => {
    const key = departmentSelectionKey(item.level, item.id)
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
  if (!uniqueItems.length) {
    return
  }
  forceDeleteDepartmentState.value = {
    visible: true,
    mode: 'batch',
    item: null,
    items: uniqueItems.map(item => ({ level: item.level, id: item.id })),
    adminPassword: '',
    submitting: false,
    error: '',
    impactText: `本次有 ${uniqueItems.length} 个部门仍有下级部门、人员或账号关联。强制删除将删除下级部门，并清空相关人员和账号部门。`,
  }
}

function departmentLevelText(level) {
  if (level === 'primary') return '一级部门'
  if (level === 'secondary') return '二级部门'
  if (level === 'tertiary') return '三级部门'
  return '部门'
}

async function toggleDepartmentUsers(nodeKey) {
  await departmentUsersRuntime.toggle(nodeKey)
}

async function loadDepartmentUsers(nodeKey, options = {}) {
  await departmentUsersRuntime.load(nodeKey, options)
}

async function handleRenamePrimary(item) {
  const nextName = window.prompt('请输入新的一级部门名称：', item.name)
  if (!nextName || nextName.trim() === item.name) {
    return
  }
  const result = await adminApi.renamePrimaryDepartment(item.id, nextName.trim())
  if (result.success) {
    setSuccess('一级部门已更新')
    await fetchDepartmentTree()
    emit('updated')
    return
  }
  error.value = result.error || '更新一级部门失败'
}

async function applyDepartmentDelete(requestDelete, successMessage, errorMessage, forceContext = null) {
  const result = await requestDelete()
  if (result.success) {
    setSuccess(result.message || successMessage)
    await refreshDepartmentChanges()
    return
  }
  if (result.code === 'DEPARTMENT_IN_USE' && forceContext) {
    error.value = result.error || errorMessage
    openSingleForceDeleteDepartment({ ...forceContext, reason: result.error })
    return
  }
  error.value = result.error || errorMessage
}

async function handleDeletePrimary(item) {
  if (!window.confirm(`确定要删除一级部门 ${item.name} 吗？仅没有二级部门、账号和人员绑定的部门可以删除。`)) {
    return
  }
  await applyDepartmentDelete(
    () => adminApi.deletePrimaryDepartment(item.id),
    '一级部门已删除',
    '删除一级部门失败',
    { level: 'primary', id: item.id, name: item.name }
  )
}

async function handleRenameSecondary(secondary) {
  const nextName = window.prompt('请输入新的二级部门名称：', secondary.name)
  if (!nextName || nextName.trim() === secondary.name) {
    return
  }
  const result = await adminApi.renameSecondaryDepartment(secondary.id, nextName.trim())
  if (result.success) {
    setSuccess('二级部门已更新')
    await fetchDepartmentTree()
    emit('updated')
    return
  }
  error.value = result.error || '更新二级部门失败'
}

async function handleDeleteSecondary(secondary) {
  if (!window.confirm(`确定要删除二级部门 ${secondary.name} 吗？仅没有三级部门、账号和人员绑定的部门可以删除。`)) {
    return
  }
  await applyDepartmentDelete(
    () => adminApi.deleteSecondaryDepartment(secondary.id),
    '二级部门已删除',
    '删除二级部门失败',
    { level: 'secondary', id: secondary.id, name: secondary.name }
  )
}

async function handleRenameTertiary(tertiary) {
  const nextName = window.prompt('请输入新的三级部门名称：', tertiary.name)
  if (!nextName || nextName.trim() === tertiary.name) {
    return
  }
  const result = await adminApi.renameTertiaryDepartment(tertiary.tertiary_id || tertiary.id, nextName.trim())
  if (result.success) {
    setSuccess('三级部门已更新')
    await fetchDepartmentTree()
    emit('updated')
    return
  }
  error.value = result.error || '更新三级部门失败'
}

async function handleDeleteTertiary(tertiary) {
  if (!window.confirm(`确定要删除三级部门 ${tertiary.name} 吗？仅没有账号和人员绑定的部门可以删除。`)) {
    return
  }
  await applyDepartmentDelete(
    () => adminApi.deleteTertiaryDepartment(tertiary.tertiary_id || tertiary.id),
    '三级部门已删除',
    '删除三级部门失败',
    { level: 'tertiary', id: tertiary.tertiary_id || tertiary.id, name: tertiary.name }
  )
}

async function handleBatchDeleteDepartments() {
  error.value = ''
  departmentBatchDeleteResult.value = null
  if (!hasSelectedDepartments.value) {
    error.value = '请至少选择一个部门'
    return
  }
  if (!window.confirm(`确定批量删除选中的 ${selectedDepartmentCount.value} 个部门吗？仅无下级、无账号/人员绑定的部门可删除，失败项不会影响其他部门。`)) {
    return
  }
  const payload = selectedDepartmentItems.value.map(item => ({ level: item.level, id: item.id }))
  const result = await adminApi.batchDeleteDepartments(payload)
  if (result.success) {
    departmentBatchDeleteResult.value = result.data
    setSuccess(formatDepartmentBatchDeleteSummary(result.data))
    openBatchForceDeleteDepartments(result.data?.details)
    if (!forceDeleteDepartmentState.value.visible) {
      clearSelectedDepartments()
    }
    await refreshDepartmentChanges()
    return
  }
  error.value = result.error || '批量删除部门失败'
}

async function submitForceDeleteDepartment() {
  const state = forceDeleteDepartmentState.value
  const adminPassword = String(state.adminPassword || '').trim()
  if (!adminPassword) {
    forceDeleteDepartmentState.value = { ...state, error: '请输入管理员密码' }
    return
  }
  forceDeleteDepartmentState.value = { ...state, submitting: true, error: '' }
  const result = state.mode === 'batch'
    ? await adminApi.batchForceDeleteDepartments(state.items, adminPassword)
    : await adminApi.forceDeleteDepartment(state.item.level, state.item.id, adminPassword)
  if (result.success) {
    if (state.mode === 'batch') {
      departmentBatchDeleteResult.value = result.data
    }
    setSuccess(result.message || '强制删除部门完成')
    clearSelectedDepartments()
    resetForceDeleteDepartmentState()
    await refreshDepartmentChanges()
    return
  }
  forceDeleteDepartmentState.value = {
    ...state,
    submitting: false,
    error: result.error || '强制删除部门失败',
  }
}

async function refreshDepartmentChanges() {
  await fetchDepartmentTree()
  emit('updated')
}

async function handleDepartmentCreated(result) {
  showDepartmentCreateDialog.value = false
  setSuccess(result?.message || '部门创建成功')
  await refreshDepartmentChanges()
}

async function handleDepartmentCreateChanged() {
  await refreshDepartmentChanges()
}

async function handleDepartmentImportSuccess(result) {
  const summary = result?.summary || {}
  departmentImportResult.value = result
  showDepartmentImportResultDialog.value = true
  setSuccess(
    `部门导入完成：成功 ${summary.success || 0} 条，失败 ${summary.failed || 0} 条，跳过 ${summary.skipped || 0} 条`
  )
  await fetchDepartmentTree()
  emit('updated')
}

onMounted(() => {
  fetchDepartmentTree()
})
</script>

<template>
  <section class="department-panel">
    <div class="panel-header">
      <div>
        <h2>部门管理</h2>
        <p>维护一级、二级、三级部门字典。删除前需先清空下级部门、账号和人员绑定。</p>
      </div>
      <div class="panel-actions">
        <div v-if="hasSelectedDepartments" class="selection-summary" aria-live="polite">
          <span>已选择</span>
          <strong>{{ selectedDepartmentCount }}</strong>
          <span>个部门</span>
        </div>
        <button class="danger-btn" :disabled="!hasSelectedDepartments" @click="handleBatchDeleteDepartments">批量删除部门</button>
        <button class="action-btn" :disabled="!hasSelectedDepartments" @click="clearSelectedDepartments">清空选择</button>
        <button class="refresh-btn" @click="fetchDepartmentTree">刷新</button>
        <button class="primary-btn" @click="showDepartmentCreateDialog = true">添加部门</button>
        <button class="import-btn" @click="showDepartmentImportDialog = true">批量导入部门</button>
      </div>
    </div>

    <div v-if="success" class="alert alert-success">{{ success }}</div>
    <div v-if="error" class="alert alert-error">{{ error }}</div>
    <div v-if="forceDeleteDepartmentState.visible" class="force-delete-card">
      <div>
        <h4>强制删除确认</h4>
        <p>{{ forceDeleteDepartmentState.impactText }}</p>
        <p class="force-delete-warning">强制删除不删除人员、不删除账号，只清空相关人员和账号部门。</p>
      </div>
      <label>
        <span>管理员密码</span>
        <input
          v-model="forceDeleteDepartmentState.adminPassword"
          type="password"
          autocomplete="current-password"
          placeholder="输入当前管理员密码"
        >
      </label>
      <div v-if="forceDeleteDepartmentState.error" class="force-delete-error">
        {{ forceDeleteDepartmentState.error }}
      </div>
      <div class="force-delete-actions">
        <button class="action-btn" :disabled="forceDeleteDepartmentState.submitting" @click="resetForceDeleteDepartmentState">
          取消
        </button>
        <button class="danger-btn" :disabled="forceDeleteDepartmentState.submitting" @click="submitForceDeleteDepartment">
          {{ forceDeleteDepartmentState.submitting ? '删除中...' : '确认强制删除' }}
        </button>
      </div>
    </div>
    <div v-if="departmentBatchDeleteResult?.details?.length" class="batch-result-card">
      <div class="batch-result-title">{{ formatDepartmentBatchDeleteSummary(departmentBatchDeleteResult) }}</div>
      <ul class="batch-result-list">
        <li
          v-for="detail in departmentBatchDeleteResult.details"
          :key="`${detail.level}-${detail.id}-${detail.row}`"
          :class="detail.status"
        >
          {{ detail.level_name || departmentLevelText(detail.level) }} /
          {{ detail.department_name || detail.id }}：{{ detail.message }}
        </li>
      </ul>
    </div>

    <div v-if="loading" class="loading">加载中...</div>

    <div v-else class="department-tree">
      <div
        v-for="primary in departmentRenderTree"
        :key="primary.id"
        class="primary-card"
      >
        <div class="primary-header">
          <div class="primary-main">
            <input
              class="department-checkbox"
              type="checkbox"
              :checked="isDepartmentSelected('primary', primary.id)"
              @change.stop="toggleDepartmentSelection('primary', primary.id, primary.name)"
            >
            <button
              type="button"
              class="collapse-toggle"
              :aria-expanded="isPrimaryExpanded(primary.id)"
              @click="togglePrimary(primary.id)"
            >
              {{ isPrimaryExpanded(primary.id) ? 'v' : '>' }}
            </button>
            <div
              class="primary-summary"
              role="button"
              tabindex="0"
              @click="togglePrimary(primary.id)"
              @keydown.enter.prevent="togglePrimary(primary.id)"
              @keydown.space.prevent="togglePrimary(primary.id)"
            >
              <div class="title-group">
                <h3>{{ primary.name }}</h3>
                <span class="status-badge" :class="primary.status">
                  {{ primary.status === 'active' ? '启用中' : '已停用' }}
                </span>
              </div>
              <span class="child-count">{{ primary.secondary_items?.length || 0 }} 个二级部门</span>
            </div>
          </div>
          <div class="actions">
            <button class="action-btn" @click="handleRenamePrimary(primary)">改名</button>
            <button class="action-btn danger-btn" @click="handleDeletePrimary(primary)">删除</button>
          </div>
        </div>

        <div v-if="isPrimaryExpanded(primary.id)" class="primary-body">
          <div v-if="primary.direct_user_count > 0" class="tertiary-list primary-direct-list">
            <div class="tertiary-item">
              <div class="secondary-header tertiary-header">
                <div class="secondary-main">
                  <span class="department-checkbox-spacer"></span>
                  <button
                    type="button"
                    class="collapse-toggle"
                    :aria-expanded="isDepartmentNodeExpanded(primaryDirectNodeKey(primary))"
                    @click="toggleDepartmentUsers(primaryDirectNodeKey(primary))"
                  >
                    {{ isDepartmentNodeExpanded(primaryDirectNodeKey(primary)) ? 'v' : '>' }}
                  </button>
                  <div
                    class="secondary-summary"
                    role="button"
                    tabindex="0"
                    @click="toggleDepartmentUsers(primaryDirectNodeKey(primary))"
                    @keydown.enter.prevent="toggleDepartmentUsers(primaryDirectNodeKey(primary))"
                    @keydown.space.prevent="toggleDepartmentUsers(primaryDirectNodeKey(primary))"
                  >
                    <div class="secondary-title">
                      <span class="secondary-name">直属一级部门成员</span>
                      <span class="status-badge active">直属</span>
                    </div>
                    <span class="secondary-count">{{ primary.direct_user_count }} 人</span>
                  </div>
                </div>
              </div>

              <div v-if="isDepartmentNodeExpanded(primaryDirectNodeKey(primary))" class="secondary-body">
                <div v-if="departmentUsersLoadingById[primaryDirectNodeKey(primary)]" class="secondary-users-state">
                  加载成员中...
                </div>
                <div
                  v-else-if="departmentUsersErrorById[primaryDirectNodeKey(primary)]"
                  class="secondary-users-state secondary-users-error"
                >
                  <span>{{ departmentUsersErrorById[primaryDirectNodeKey(primary)] }}</span>
                  <button class="action-btn" @click="loadDepartmentUsers(primaryDirectNodeKey(primary), { force: true })">重试</button>
                </div>
                <div v-else-if="departmentUsersById[primaryDirectNodeKey(primary)]?.length" class="secondary-users">
                  <div class="secondary-users-head">
                    <span>工号</span>
                    <span>姓名</span>
                    <span>状态</span>
                  </div>
                  <div
                    v-for="member in departmentUsersById[primaryDirectNodeKey(primary)]"
                    :key="member.id"
                    class="secondary-user-row"
                  >
                    <span>{{ member.employee_no }}</span>
                    <span>{{ member.full_name }}</span>
                    <span class="status-badge" :class="member.status">
                      {{ member.status === 'active' ? '启用中' : '已停用' }}
                    </span>
                  </div>
                </div>
                <p v-else class="secondary-users-state">暂无成员</p>
              </div>
            </div>
          </div>

          <div v-if="primary.renderSecondaryItems?.length" class="secondary-list">
            <div
              v-for="secondary in primary.renderSecondaryItems"
              :key="secondary.id"
              class="secondary-item"
            >
              <div class="secondary-header">
                <div class="secondary-main">
                  <input
                    class="department-checkbox"
                    type="checkbox"
                    :checked="isDepartmentSelected('secondary', secondary.id)"
                    @change.stop="toggleDepartmentSelection('secondary', secondary.id, secondary.name)"
                  >
                  <button
                    type="button"
                    class="collapse-toggle"
                    :aria-expanded="isSecondaryExpanded(secondary.id)"
                    @click="toggleSecondary(secondary.id)"
                  >
                    {{ isSecondaryExpanded(secondary.id) ? 'v' : '>' }}
                  </button>
                  <div
                    class="secondary-summary"
                    role="button"
                    tabindex="0"
                    @click="toggleSecondary(secondary.id)"
                    @keydown.enter.prevent="toggleSecondary(secondary.id)"
                    @keydown.space.prevent="toggleSecondary(secondary.id)"
                  >
                    <div class="secondary-title">
                      <span class="secondary-name">{{ secondary.name }}</span>
                      <span class="status-badge" :class="secondary.effectiveStatus">
                        {{ secondary.effectiveStatus === 'active' ? '可选' : '已停用' }}
                      </span>
                    </div>
                    <span class="secondary-count">
                      {{ secondary.tertiary_count }} 个三级部门 / {{ secondary.user_count }} 人
                    </span>
                  </div>
                </div>
                <div class="secondary-meta">
                  <span v-if="secondary.direct_user_count > 0" class="meta-text">
                    直属二级 {{ secondary.direct_user_count }} 人
                  </span>
                  <div class="actions">
                    <button class="action-btn" @click="handleRenameSecondary(secondary.raw)">改名</button>
                    <button class="action-btn danger-btn" @click="handleDeleteSecondary(secondary.raw)">删除</button>
                  </div>
                </div>
              </div>

              <div v-if="isSecondaryExpanded(secondary.id)" class="secondary-body">
                <div v-if="secondary.children?.length" class="tertiary-list">
                  <div
                    v-for="tertiary in secondary.children"
                    :key="tertiary.nodeKey"
                    class="tertiary-item"
                  >
                    <div class="secondary-header tertiary-header">
                      <div class="secondary-main">
                        <input
                          v-if="tertiary.nodeType === 'tertiary'"
                          class="department-checkbox"
                          type="checkbox"
                          :checked="isDepartmentSelected('tertiary', tertiary.tertiary_id || tertiary.id)"
                          @change.stop="toggleDepartmentSelection('tertiary', tertiary.tertiary_id || tertiary.id, tertiary.name)"
                        >
                        <span v-else class="department-checkbox-spacer"></span>
                        <button
                          type="button"
                          class="collapse-toggle"
                          :aria-expanded="isDepartmentNodeExpanded(tertiary.nodeKey)"
                          @click="toggleDepartmentUsers(tertiary.nodeKey)"
                        >
                          {{ isDepartmentNodeExpanded(tertiary.nodeKey) ? 'v' : '>' }}
                        </button>
                        <div
                          class="secondary-summary"
                          role="button"
                          tabindex="0"
                          @click="toggleDepartmentUsers(tertiary.nodeKey)"
                          @keydown.enter.prevent="toggleDepartmentUsers(tertiary.nodeKey)"
                          @keydown.space.prevent="toggleDepartmentUsers(tertiary.nodeKey)"
                        >
                          <div class="secondary-title">
                            <span class="secondary-name">
                              {{ departmentNodeName(tertiary) }}
                            </span>
                            <span
                              class="status-badge"
                              :class="departmentNodeStatusClass(tertiary)"
                            >
                              {{ departmentNodeStatusLabel(tertiary) }}
                            </span>
                          </div>
                          <span class="secondary-count">{{ tertiary.user_count }} 人</span>
                        </div>
                      </div>
                      <div class="actions" v-if="tertiary.nodeType === 'tertiary'">
                        <button class="action-btn" @click="handleRenameTertiary(tertiary)">改名</button>
                        <button class="action-btn danger-btn" @click="handleDeleteTertiary(tertiary)">删除</button>
                      </div>
                    </div>

                    <div v-if="isDepartmentNodeExpanded(tertiary.nodeKey)" class="secondary-body">
                      <div v-if="departmentUsersLoadingById[tertiary.nodeKey]" class="secondary-users-state">
                        加载成员中...
                      </div>
                      <div
                        v-else-if="departmentUsersErrorById[tertiary.nodeKey]"
                        class="secondary-users-state secondary-users-error"
                      >
                        <span>{{ departmentUsersErrorById[tertiary.nodeKey] }}</span>
                        <button class="action-btn" @click="loadDepartmentUsers(tertiary.nodeKey, { force: true })">重试</button>
                      </div>
                      <div v-else-if="departmentUsersById[tertiary.nodeKey]?.length" class="secondary-users">
                        <div class="secondary-users-head">
                          <span>工号</span>
                          <span>姓名</span>
                          <span>状态</span>
                        </div>
                        <div
                          v-for="member in departmentUsersById[tertiary.nodeKey]"
                          :key="member.id"
                          class="secondary-user-row"
                        >
                          <span>{{ member.employee_no }}</span>
                          <span>{{ member.full_name }}</span>
                          <span class="status-badge" :class="member.status">
                            {{ member.status === 'active' ? '启用中' : '已停用' }}
                          </span>
                        </div>
                      </div>
                      <p v-else class="secondary-users-state">暂无成员</p>
                    </div>
                  </div>
                </div>
                <p v-else class="secondary-users-state">暂无三级部门</p>
              </div>
            </div>
          </div>

          <p v-else class="empty-secondary">暂无二级部门</p>
        </div>
      </div>
    </div>

    <DepartmentCreateDialog
      :show="showDepartmentCreateDialog"
      :department-tree="departmentTree"
      @close="showDepartmentCreateDialog = false"
      @created="handleDepartmentCreated"
      @changed="handleDepartmentCreateChanged"
    />
    <DepartmentBatchImportDialog
      :show="showDepartmentImportDialog"
      @close="showDepartmentImportDialog = false"
      @import-success="handleDepartmentImportSuccess"
    />
    <DepartmentImportResultDialog
      :show="showDepartmentImportResultDialog"
      :result="departmentImportResult"
      @close="showDepartmentImportResultDialog = false"
    />
  </section>
</template>

<style scoped>
.department-panel {
  background: white;
  border-radius: 12px;
  padding: 24px;
  margin-bottom: 24px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
}

.panel-header {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
}

.panel-actions {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  align-items: center;
}

.panel-header h2 {
  margin: 0 0 8px 0;
  color: #1f2937;
}

.panel-header p {
  margin: 0;
  font-size: 14px;
  color: #6b7280;
}

.refresh-btn,
.primary-btn,
.import-btn,
.action-btn {
  border: 1px solid #d1d5db;
  border-radius: 8px;
  background: white;
  color: #1f2937;
  cursor: pointer;
  padding: 8px 14px;
}

.refresh-btn:disabled,
.primary-btn:disabled,
.import-btn:disabled,
.action-btn:disabled,
.danger-btn:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.primary-btn {
  background: #667eea;
  border-color: #667eea;
  color: white;
}

.import-btn {
  background: #0f766e;
  border-color: #0f766e;
  color: white;
}

.danger-btn {
  border: 1px solid #fecaca;
  border-radius: 8px;
  background: white;
  border-color: #fecaca;
  color: #dc2626;
  cursor: pointer;
  padding: 8px 14px;
}

.danger-btn:hover:not(:disabled) {
  background: #fef2f2;
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

.force-delete-card {
  display: grid;
  gap: 12px;
  border: 1px solid #fecaca;
  border-radius: 8px;
  background: #fff7f7;
  margin-bottom: 16px;
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

.batch-result-card {
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  background: #fafafa;
  margin-bottom: 16px;
  padding: 14px 16px;
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
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: white;
  color: #374151;
  padding: 10px 12px;
}

.batch-result-list li.success {
  border-color: #bbf7d0;
  background: #f0fdf4;
}

.batch-result-list li.failed {
  border-color: #fecaca;
  background: #fef2f2;
}

.alert {
  padding: 12px 16px;
  border-radius: 8px;
  margin-bottom: 16px;
}

.alert-success {
  background: #dcfce7;
  color: #166534;
}

.alert-error {
  background: #fef2f2;
  color: #dc2626;
}

.department-tree {
  display: grid;
  gap: 16px;
}

.primary-card {
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  padding: 16px;
  background: #fafafa;
}

.primary-main {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
  flex: 1;
}

.primary-header,
.secondary-header,
.secondary-meta,
.secondary-main {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
}

.primary-header {
  margin-bottom: 0;
}

.primary-summary {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex: 1;
  min-width: 0;
  cursor: pointer;
}

.collapse-toggle {
  width: 32px;
  height: 32px;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  background: white;
  color: #374151;
  cursor: pointer;
  font-family: monospace;
  font-size: 16px;
}

.department-checkbox,
.department-checkbox-spacer {
  flex: 0 0 auto;
  width: 18px;
  height: 18px;
}

.department-checkbox {
  cursor: pointer;
}

.child-count {
  color: #6b7280;
  font-size: 13px;
  white-space: nowrap;
}

.title-group {
  display: flex;
  align-items: center;
  gap: 10px;
}

.secondary-title {
  display: flex;
  align-items: center;
  gap: 10px;
}

.title-group h3,
.secondary-name {
  margin: 0;
  color: #1f2937;
}

.status-badge {
  border-radius: 999px;
  padding: 4px 10px;
  font-size: 12px;
}

.status-badge.active {
  background: #dcfce7;
  color: #166534;
}

.status-badge.disabled {
  background: #fee2e2;
  color: #dc2626;
}

.primary-body {
  display: grid;
  gap: 14px;
  margin-top: 14px;
}

.secondary-list,
.tertiary-list {
  display: grid;
  gap: 10px;
}

.secondary-item,
.tertiary-item {
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  padding: 12px;
  background: white;
}

.secondary-summary {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex: 1;
  min-width: 0;
  cursor: pointer;
}

.secondary-count {
  color: #6b7280;
  font-size: 13px;
  white-space: nowrap;
}

.secondary-body {
  display: grid;
  gap: 10px;
  margin-top: 12px;
}

.secondary-users {
  display: grid;
  gap: 8px;
}

.secondary-users-head,
.secondary-user-row {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr) auto;
  gap: 12px;
  align-items: center;
}

.secondary-users-head {
  color: #6b7280;
  font-size: 12px;
}

.secondary-user-row {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 10px 12px;
  background: #f9fafb;
  color: #1f2937;
  font-size: 13px;
}

.secondary-users-state {
  color: #6b7280;
  font-size: 13px;
}

.secondary-users-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.meta-text,
.empty-secondary,
.loading {
  color: #6b7280;
  font-size: 13px;
}

.loading {
  padding: 20px 0;
}

@media (max-width: 768px) {
  .panel-header,
  .panel-actions,
  .primary-main,
  .primary-summary,
  .primary-header,
  .secondary-header,
  .secondary-summary,
  .secondary-meta {
    flex-direction: column;
    align-items: stretch;
  }

  .title-group,
  .secondary-title,
  .secondary-main {
    justify-content: flex-start;
    flex-wrap: wrap;
  }

  .secondary-users-head,
  .secondary-user-row {
    grid-template-columns: 1fr;
  }
}
</style>
