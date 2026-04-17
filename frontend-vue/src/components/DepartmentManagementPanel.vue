<script setup>
import { onMounted, ref } from 'vue'
import { adminApi } from '../services/admin'
import DepartmentBatchImportDialog from './DepartmentBatchImportDialog.vue'
import DepartmentImportResultDialog from './DepartmentImportResultDialog.vue'
import { createSecondaryUsersRuntime } from '../utils/departmentSecondaryUsersRuntime'

const emit = defineEmits(['updated'])

const loading = ref(false)
const error = ref('')
const success = ref('')
const departmentTree = ref([])
const newPrimaryName = ref('')
const secondaryDrafts = ref({})
const showDepartmentImportDialog = ref(false)
const showDepartmentImportResultDialog = ref(false)
const departmentImportResult = ref(null)
const expandedPrimaryIds = ref([])
const secondaryUsersRuntime = createSecondaryUsersRuntime({
  requestUsers: (secondaryId) => adminApi.getSecondaryDepartmentUsers(secondaryId),
})
const expandedSecondaryIds = secondaryUsersRuntime.expandedIds
const secondaryUsersById = secondaryUsersRuntime.usersById
const secondaryUsersLoadingById = secondaryUsersRuntime.loadingById
const secondaryUsersErrorById = secondaryUsersRuntime.errorById

function setSuccess(message) {
  success.value = message
  error.value = ''
  setTimeout(() => {
    success.value = ''
  }, 3000)
}

async function fetchDepartmentTree() {
  loading.value = true
  const result = await adminApi.getDepartmentTree()
  if (result.success) {
    departmentTree.value = Array.isArray(result.data?.items) ? result.data.items : []
    expandedPrimaryIds.value = []
    secondaryUsersRuntime.reset()
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
    expandedPrimaryIds.value = expandedPrimaryIds.value.filter((item) => item !== normalizedId)
    return
  }
  expandedPrimaryIds.value = [...expandedPrimaryIds.value, normalizedId]
}

function isSecondaryExpanded(secondaryId) {
  return secondaryUsersRuntime.isExpanded(secondaryId)
}

async function toggleSecondary(secondaryId) {
  await secondaryUsersRuntime.toggle(secondaryId)
}

async function loadSecondaryUsers(secondaryId, options = {}) {
  await secondaryUsersRuntime.load(secondaryId, options)
}

async function handleCreatePrimary() {
  const name = String(newPrimaryName.value || '').trim()
  if (!name) {
    error.value = '请输入一级部门名称'
    return
  }
  const result = await adminApi.createPrimaryDepartment(name)
  if (result.success) {
    newPrimaryName.value = ''
    setSuccess('一级部门创建成功')
    await fetchDepartmentTree()
    emit('updated')
    return
  }
  error.value = result.error || '创建一级部门失败'
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

async function handleTogglePrimaryStatus(item) {
  const targetStatus = item.status === 'active' ? 'disabled' : 'active'
  const actionText = targetStatus === 'disabled' ? '停用' : '启用'
  if (!window.confirm(`确定要${actionText}一级部门 ${item.name} 吗？`)) {
    return
  }
  const result = await adminApi.updatePrimaryDepartmentStatus(item.id, targetStatus)
  if (result.success) {
    setSuccess(`一级部门已${actionText}`)
    await fetchDepartmentTree()
    emit('updated')
    return
  }
  error.value = result.error || `一级部门${actionText}失败`
}

async function handleCreateSecondary(primary) {
  const name = String(secondaryDrafts.value[primary.id] || '').trim()
  if (!name) {
    error.value = '请输入二级部门名称'
    return
  }
  const result = await adminApi.createSecondaryDepartment(primary.id, name)
  if (result.success) {
    secondaryDrafts.value = {
      ...secondaryDrafts.value,
      [primary.id]: '',
    }
    setSuccess('二级部门创建成功')
    await fetchDepartmentTree()
    emit('updated')
    return
  }
  error.value = result.error || '创建二级部门失败'
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

async function handleToggleSecondaryStatus(secondary) {
  const targetStatus = secondary.status === 'active' ? 'disabled' : 'active'
  const actionText = targetStatus === 'disabled' ? '停用' : '启用'
  if (!window.confirm(`确定要${actionText}二级部门 ${secondary.name} 吗？`)) {
    return
  }
  const result = await adminApi.updateSecondaryDepartmentStatus(secondary.id, targetStatus)
  if (result.success) {
    setSuccess(`二级部门已${actionText}`)
    await fetchDepartmentTree()
    emit('updated')
    return
  }
  error.value = result.error || `二级部门${actionText}失败`
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
        <p>维护一级、二级部门字典。停用后不会清空已绑定用户，只会禁止后续新选择。</p>
      </div>
      <div class="panel-actions">
        <button class="refresh-btn" @click="fetchDepartmentTree">刷新</button>
        <button class="import-btn" @click="showDepartmentImportDialog = true">批量导入部门</button>
      </div>
    </div>

    <div v-if="success" class="alert alert-success">{{ success }}</div>
    <div v-if="error" class="alert alert-error">{{ error }}</div>

    <div class="create-primary">
      <input v-model="newPrimaryName" type="text" placeholder="新增一级部门，例如：计算机学院">
      <button class="primary-btn" @click="handleCreatePrimary">新增一级部门</button>
    </div>

    <div v-if="loading" class="loading">加载中...</div>

    <div v-else class="department-tree">
      <div
        v-for="primary in departmentTree"
        :key="primary.id"
        class="primary-card"
      >
        <div class="primary-header">
          <div class="primary-main">
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
            <button class="action-btn" @click="handleTogglePrimaryStatus(primary)">
              {{ primary.status === 'active' ? '停用' : '启用' }}
            </button>
          </div>
        </div>

        <div v-if="isPrimaryExpanded(primary.id)" class="primary-body">
          <div class="create-secondary">
            <input
              v-model="secondaryDrafts[primary.id]"
              type="text"
              :placeholder="`在 ${primary.name} 下新增二级部门`"
            >
            <button class="secondary-btn" @click="handleCreateSecondary(primary)">新增二级部门</button>
          </div>

          <div v-if="primary.secondary_items?.length" class="secondary-list">
            <div
              v-for="secondary in primary.secondary_items"
              :key="secondary.id"
              class="secondary-item"
            >
              <div class="secondary-header">
                <div class="secondary-main">
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
                      <span class="status-badge" :class="secondary.effective_status">
                        {{ secondary.effective_status === 'active' ? '可选' : '已停用' }}
                      </span>
                    </div>
                    <span class="secondary-count">{{ secondary.user_count || 0 }} 人</span>
                  </div>
                </div>
                <div class="secondary-meta">
                  <span v-if="secondary.effective_status !== secondary.status" class="meta-text">
                    子级存储状态 {{ secondary.status === 'active' ? '启用' : '停用' }}，当前随一级部门禁用
                  </span>
                  <div class="actions">
                    <button class="action-btn" @click="handleRenameSecondary(secondary)">改名</button>
                    <button class="action-btn" @click="handleToggleSecondaryStatus(secondary)">
                      {{ secondary.status === 'active' ? '停用' : '启用' }}
                    </button>
                  </div>
                </div>
              </div>

              <div v-if="isSecondaryExpanded(secondary.id)" class="secondary-body">
                <div v-if="secondaryUsersLoadingById[secondary.id]" class="secondary-users-state">
                  加载用户中...
                </div>
                <div v-else-if="secondaryUsersErrorById[secondary.id]" class="secondary-users-state secondary-users-error">
                  <span>{{ secondaryUsersErrorById[secondary.id] }}</span>
                  <button class="action-btn" @click="loadSecondaryUsers(secondary.id, { force: true })">重试</button>
                </div>
                <div v-else-if="secondaryUsersById[secondary.id]?.length" class="secondary-users">
                  <div class="secondary-users-head">
                    <span>用户名</span>
                    <span>用户类型</span>
                    <span>状态</span>
                  </div>
                  <div
                    v-for="user in secondaryUsersById[secondary.id]"
                    :key="user.id"
                    class="secondary-user-row"
                  >
                    <span>{{ user.username }}</span>
                    <span>{{ user.user_type_label }}</span>
                    <span class="status-badge" :class="user.status">
                      {{ user.status === 'active' ? '启用中' : '已停用' }}
                    </span>
                  </div>
                </div>
                <p v-else class="secondary-users-state">暂无用户</p>
              </div>
            </div>
          </div>

          <p v-else class="empty-secondary">暂无二级部门</p>
        </div>
      </div>
    </div>

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
.secondary-btn,
.action-btn {
  border: 1px solid #d1d5db;
  border-radius: 8px;
  background: white;
  color: #1f2937;
  cursor: pointer;
  padding: 8px 14px;
}

.primary-btn,
.secondary-btn {
  background: #667eea;
  border-color: #667eea;
  color: white;
}

.import-btn {
  background: #0f766e;
  border-color: #0f766e;
  color: white;
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

.create-primary,
.create-secondary {
  display: flex;
  gap: 12px;
}

.create-primary {
  margin-bottom: 20px;
}

.create-primary input,
.create-secondary input {
  flex: 1;
  padding: 10px 12px;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  font-size: 14px;
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

.secondary-list {
  display: grid;
  gap: 10px;
}

.secondary-item {
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
  .create-primary,
  .create-secondary,
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
