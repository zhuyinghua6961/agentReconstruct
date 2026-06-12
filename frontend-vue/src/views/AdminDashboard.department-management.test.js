import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const currentDir = dirname(fileURLToPath(import.meta.url))
function readSource(relativePath) {
  const fullPath = join(currentDir, ...relativePath)
  if (!existsSync(fullPath)) {
    return ''
  }
  return readFileSync(fullPath, 'utf8')
}

function sourceBlockAfter(source, marker) {
  const markerIndex = source.indexOf(marker)
  if (markerIndex === -1) {
    return ''
  }
  const blockStart = source.indexOf('{', markerIndex)
  if (blockStart === -1) {
    return ''
  }
  let depth = 0
  for (let index = blockStart; index < source.length; index += 1) {
    const char = source[index]
    if (char === '{') depth += 1
    if (char === '}') depth -= 1
    if (depth === 0) {
      return source.slice(blockStart, index + 1)
    }
  }
  return ''
}

const adminSource = readSource(['AdminDashboard.vue'])
const batchImportSource = readSource(['..', 'components', 'BatchImportDialog.vue'])
const importResultSource = readSource(['..', 'components', 'ImportResultDialog.vue'])
const departmentBatchImportSource = readSource(['..', 'components', 'DepartmentBatchImportDialog.vue'])
const departmentCreateSource = readSource(['..', 'components', 'DepartmentCreateDialog.vue'])
const departmentImportResultSource = readSource(['..', 'components', 'DepartmentImportResultDialog.vue'])
const forceDeleteDialogSource = readSource(['..', 'components', 'ForceDeleteConfirmDialog.vue'])
const panelSource = readSource(['..', 'components', 'DepartmentManagementPanel.vue'])
const adminServiceSource = readSource(['..', 'services', 'admin.js'])
const runtimeSource = readSource(['..', 'utils', 'departmentSecondaryUsersRuntime.js'])
const routerSource = readSource(['..', 'router', 'index.js'])

test('AdminDashboard includes department management entry and user department column', () => {
  assert.match(adminSource, /DepartmentManagementPanel/)
  assert.match(adminSource, /部门管理/)
  assert.match(adminSource, /<th>\s*部门\s*<\/th>/)
})

test('AdminDashboard removes account-level department assignment flows while keeping department queries', () => {
  assert.doesNotMatch(adminSource, /DepartmentSelector/)
  assert.doesNotMatch(adminSource, /showDepartmentModal/)
  assert.doesNotMatch(adminSource, /openDepartmentModal/)
  assert.doesNotMatch(adminSource, /submitUserDepartment/)
  assert.doesNotMatch(adminSource, /newTertiaryDepartmentId/)
  assert.doesNotMatch(adminSource, /editTertiaryDepartmentId/)
  assert.match(adminServiceSource, /getDepartmentTree/)
  assert.doesNotMatch(adminServiceSource, /updateUserDepartment/)
  assert.match(adminServiceSource, /getTertiaryDepartmentUsers/)
  assert.match(adminServiceSource, /getSecondaryLegacyDepartmentUsers/)
})

test('admin service still exposes department dictionary queries without account-level department mutation api', () => {
  assert.match(adminServiceSource, /getDepartmentTree/)
  assert.doesNotMatch(adminServiceSource, /updateUserDepartment/)
})

test('AdminDashboard wires username editing into user management flows', () => {
  assert.match(adminSource, /修改用户名/)
  assert.match(adminSource, /updateUserUsername/)
  assert.match(adminSource, /!isAdminIdentity\(user\)/)
})

test('AdminDashboard account list keeps reset password but removes direct password edit action', () => {
  assert.match(adminSource, /重置密码/)
  assert.match(adminSource, /openResetPasswordModal\(user\)/)
  assert.doesNotMatch(adminSource, /@click="openPasswordModal\(user\)"/)
})

test('BatchImportDialog documents the simplified user import template without department columns', () => {
  assert.match(batchImportSource, /用户名、密码、用户类型/)
  assert.match(batchImportSource, /也兼容旧列名 username、password、user_type/)
  assert.match(batchImportSource, /以用户名作为匹配键/)
  assert.match(batchImportSource, /内容未变化则跳过/)
  assert.match(batchImportSource, /密码或用户类型变化则更新/)
  assert.doesNotMatch(batchImportSource, /primary_department_name/)
  assert.doesNotMatch(batchImportSource, /secondary_department_name/)
})

test('account import result dialog supports updated rows', () => {
  assert.match(importResultSource, /updatedCount/)
  assert.match(importResultSource, /updated/)
  assert.match(importResultSource, /更新/)
  assert.match(adminSource, /summary\.updated/)
})

test('admin service exposes department import APIs', () => {
  assert.match(adminServiceSource, /batchImportDepartments/)
  assert.match(adminServiceSource, /downloadDepartmentImportTemplate/)
})

test('DepartmentBatchImportDialog documents status columns', () => {
  assert.match(departmentBatchImportSource, /一级状态/)
  assert.match(departmentBatchImportSource, /二级状态/)
  assert.match(departmentBatchImportSource, /三级状态/)
  assert.match(departmentBatchImportSource, /active/)
  assert.match(departmentBatchImportSource, /disabled/)
})

test('Department import result dialog supports updated rows and summary', () => {
  assert.match(departmentImportResultSource, /updatedCount/)
  assert.match(departmentImportResultSource, /updated/)
  assert.match(departmentImportResultSource, /status-updated/)
  assert.match(departmentImportResultSource, /summary\.value\?\.updated/)
  assert.match(panelSource, /summary\.updated/)
})

test('DepartmentBatchImportDialog guards closing during upload and resets file input', () => {
  assert.match(departmentBatchImportSource, /function requestClose\(\)/)
  assert.match(departmentBatchImportSource, /if \(uploading\.value\)/)
  assert.match(departmentBatchImportSource, /@click\.self="requestClose"/)
  assert.match(departmentBatchImportSource, /resetFileInput\(\)/)
  assert.match(departmentBatchImportSource, /fileInput\.value\.value = ''/)
})

test('DepartmentManagementPanel refreshes the dictionary after department import success', () => {
  assert.match(panelSource, /handleDepartmentImportSuccess/)
  assert.match(panelSource, /await fetchDepartmentTree\(\)/)
  assert.match(panelSource, /DepartmentImportResultDialog/)
})

test('DepartmentManagementPanel shows department batch import entry', () => {
  assert.match(panelSource, /批量导入部门/)
  assert.match(panelSource, /DepartmentBatchImportDialog/)
})

test('DepartmentManagementPanel uses one dialog entry for department creation', () => {
  assert.match(panelSource, /DepartmentCreateDialog/)
  assert.match(panelSource, /showDepartmentCreateDialog/)
  assert.match(panelSource, /@created="handleDepartmentCreated"/)
  assert.match(panelSource, />添加部门</)
  assert.doesNotMatch(panelSource, /newPrimaryName/)
  assert.doesNotMatch(panelSource, /secondaryDrafts/)
  assert.doesNotMatch(panelSource, /tertiaryDrafts/)
  assert.doesNotMatch(panelSource, /class="create-primary"/)
  assert.doesNotMatch(panelSource, /class="create-secondary"/)
})

test('DepartmentManagementPanel exposes delete and status actions for departments', () => {
  assert.match(adminServiceSource, /deletePrimaryDepartment/)
  assert.match(adminServiceSource, /deleteSecondaryDepartment/)
  assert.match(adminServiceSource, /deleteTertiaryDepartment/)
  assert.match(adminServiceSource, /updatePrimaryDepartmentStatus/)
  assert.match(adminServiceSource, /updateSecondaryDepartmentStatus/)
  assert.match(adminServiceSource, /updateTertiaryDepartmentStatus/)
  assert.match(panelSource, /handleDeletePrimary/)
  assert.match(panelSource, /handleDeleteSecondary/)
  assert.match(panelSource, /handleDeleteTertiary/)
  assert.match(panelSource, /handleTogglePrimaryStatus/)
  assert.match(panelSource, /handleToggleSecondaryStatus/)
  assert.match(panelSource, /handleToggleTertiaryStatus/)
  assert.match(panelSource, />删除</)
})

test('DepartmentManagementPanel supports selecting mixed department levels for batch delete', () => {
  assert.match(adminServiceSource, /batchDeleteDepartments/)
  assert.match(adminServiceSource, /departments\/batch-delete/)
  assert.match(adminServiceSource, /batchUpdateDepartmentStatus/)
  assert.match(adminServiceSource, /departments\/batch-status/)
  assert.match(panelSource, /selectedDepartmentItems/)
  assert.match(panelSource, /departmentSelectionKey/)
  assert.match(panelSource, /collectSelectableDepartments/)
  assert.match(panelSource, /toggleDepartmentSelection/)
  assert.match(panelSource, /handleBatchDeleteDepartments/)
  assert.match(panelSource, /handleBatchUpdateDepartmentStatus/)
  assert.match(panelSource, /批量删除部门/)
  assert.match(panelSource, /批量启用部门/)
  assert.match(panelSource, /批量停用部门/)
  assert.match(panelSource, /批量启停部门结果/)
  assert.match(panelSource, /仅无下级、无账号\/人员绑定的部门可删除/)
  assert.match(panelSource, /department-checkbox/)
  assert.match(panelSource, /primary/)
  assert.match(panelSource, /secondary/)
  assert.match(panelSource, /tertiary/)
  assert.match(panelSource, /ImportResultDialog/)
  assert.match(panelSource, /departmentBatchOperationResult/)
  assert.match(panelSource, /openDepartmentBatchOperationResult/)
  assert.match(panelSource, /批量删除部门结果/)
  assert.match(panelSource, /批量强制删除部门结果/)
  assert.doesNotMatch(panelSource, /departmentBatchDeleteResult/)
  assert.doesNotMatch(panelSource, /batch-result-card/)
})

test('DepartmentManagementPanel upgrades in-use delete failures to password-confirmed force delete', () => {
  assert.match(panelSource, /ForceDeleteConfirmDialog/)
  assert.match(panelSource, /forceDeleteDepartmentState/)
  assert.match(panelSource, /adminPassword/)
  assert.match(panelSource, /DEPARTMENT_IN_USE/)
  assert.match(panelSource, /submitForceDeleteDepartment/)
  assert.match(panelSource, /forceDeleteDepartment/)
  assert.match(panelSource, /batchForceDeleteDepartments/)
  assert.match(panelSource, /将删除下级部门/)
  assert.match(panelSource, /清空相关人员和账号部门/)
  assert.doesNotMatch(panelSource, /class="force-delete-card"/)
  assert.match(adminServiceSource, /forceDeleteDepartment\(/)
  assert.match(adminServiceSource, /departments\/\$\{level\}\/\$\{departmentId\}\/force-delete/)
  assert.match(adminServiceSource, /batchForceDeleteDepartments\(/)
  assert.match(adminServiceSource, /departments\/batch-force-delete/)
})

test('Department batch delete waits for force-delete password before showing force result', () => {
  assert.match(
    panelSource,
    /openBatchForceDeleteDepartments\(result\.data\?\.details\)[\s\S]*if \(forceDeleteDepartmentState\.value\.visible\) \{[\s\S]*await refreshDepartmentChanges\(\)[\s\S]*return[\s\S]*\}[\s\S]*openDepartmentBatchOperationResult\('批量删除部门结果', result\.data\)/,
  )
  const forceDeleteBranch = sourceBlockAfter(panelSource, 'if (forceDeleteDepartmentState.value.visible)')
  assert.doesNotMatch(forceDeleteBranch, /setSuccess\(/)
})

test('ForceDeleteConfirmDialog renders department force delete confirmation as a modal', () => {
  assert.match(forceDeleteDialogSource, /class="modal-overlay"/)
  assert.match(forceDeleteDialogSource, /role="dialog"/)
  assert.match(forceDeleteDialogSource, /管理员密码/)
  assert.match(forceDeleteDialogSource, /type="password"/)
  assert.match(forceDeleteDialogSource, /确认强制删除/)
  assert.match(forceDeleteDialogSource, /emit\('confirm'/)
  assert.match(forceDeleteDialogSource, /emit\('cancel'/)
})

test('DepartmentCreateDialog supports existing and new parent choices with ordered creation api calls', () => {
  assert.match(departmentCreateSource, /primaryMode/)
  assert.match(departmentCreateSource, /secondaryMode/)
  assert.match(departmentCreateSource, /DEFAULT_DEPARTMENT_CREATE_TARGET_LEVEL/)
  assert.match(departmentCreateSource, /createPrimaryDepartment/)
  assert.match(departmentCreateSource, /createSecondaryDepartment/)
  assert.match(departmentCreateSource, /createTertiaryDepartment/)
  assert.match(departmentCreateSource, /await ensurePrimaryDepartmentId/)
  assert.match(departmentCreateSource, /await ensureSecondaryDepartmentId/)
  assert.match(departmentCreateSource, /emit\('created'/)
  assert.match(departmentCreateSource, /已有一级部门/)
  assert.match(departmentCreateSource, /新增一级部门/)
  assert.match(departmentCreateSource, /已有二级部门/)
  assert.match(departmentCreateSource, /新增二级部门/)
})

test('Department import result dialog shows department columns', () => {
  assert.match(departmentImportResultSource, /一级部门/)
  assert.match(departmentImportResultSource, /一级状态/)
  assert.match(departmentImportResultSource, /二级部门/)
  assert.match(departmentImportResultSource, /二级状态/)
  assert.match(departmentImportResultSource, /三级部门/)
  assert.match(departmentImportResultSource, /三级状态/)
})

test('DepartmentManagementPanel renders collapsible primary department sections', () => {
  assert.match(panelSource, /expandedPrimaryIds/)
  assert.match(panelSource, /togglePrimary/)
  assert.match(panelSource, /isPrimaryExpanded/)
  assert.match(panelSource, /collapse-toggle/)
  assert.match(panelSource, /child-count/)
  assert.match(panelSource, /isPrimaryExpanded\(primary\.id\)/)
})

test('admin service exposes direct and tertiary department user query api', () => {
  assert.match(adminServiceSource, /getTertiaryDepartmentUsers/)
  assert.match(adminServiceSource, /departments\/tertiary\/\$\{tertiaryId\}\/users/)
  assert.match(adminServiceSource, /getPrimaryDirectDepartmentUsers/)
  assert.match(adminServiceSource, /departments\/primary\/\$\{primaryId\}\/direct-users/)
  assert.match(adminServiceSource, /getSecondaryDirectDepartmentUsers/)
  assert.match(adminServiceSource, /departments\/secondary\/\$\{secondaryId\}\/direct-users/)
})

test('DepartmentManagementPanel renders collapsible secondary and tertiary sections with member counts', () => {
  assert.match(panelSource, /expandedSecondaryIds/)
  assert.match(panelSource, /toggleSecondary/)
  assert.match(panelSource, /isSecondaryExpanded/)
  assert.match(panelSource, /secondary\.tertiary_count/)
  assert.match(panelSource, /direct_user_count/)
  assert.match(panelSource, /直属一级部门成员/)
  assert.match(panelSource, /直属二级部门成员/)
  assert.match(panelSource, /工号/)
  assert.match(panelSource, /姓名/)
  assert.doesNotMatch(panelSource, /未补全三级部门用户/)
  assert.doesNotMatch(panelSource, /未补全三级/)
  assert.doesNotMatch(panelSource, /用户名/)
  assert.doesNotMatch(panelSource, /用户类型/)
  assert.match(panelSource, /tertiary\.user_count/)
})

test('DepartmentManagementPanel lazy loads direct and tertiary members with local loading and error states', () => {
  assert.match(panelSource, /createDepartmentUsersRuntime/)
  assert.match(panelSource, /loadDepartmentUsers/)
  assert.match(panelSource, /departmentUsersById/)
  assert.match(panelSource, /departmentUsersLoadingById/)
  assert.match(panelSource, /departmentUsersErrorById/)
  assert.match(runtimeSource, /获取成员列表失败/)
  assert.match(panelSource, /暂无成员/)
})

test('AdminDashboard promotes quota, users, and departments into top admin tabs', () => {
  assert.match(adminSource, /activeAdminTab/)
  assert.match(adminSource, /setAdminTab/)
  assert.match(adminSource, /QuotaManagementPanel/)
  assert.match(adminSource, /个人中心/)
  assert.match(adminSource, /配额管理/)
  assert.match(adminSource, /用户管理/)
  assert.match(adminSource, /部门管理/)
})

test('AdminDashboard adds user management secondary tabs for accounts and personnel', () => {
  assert.match(adminSource, /activeUserManagementTab/)
  assert.match(adminSource, /账号列表/)
  assert.match(adminSource, /人员表/)
})

test('AdminDashboard mounts PersonnelManagementPanel under user management', () => {
  assert.match(adminSource, /PersonnelManagementPanel/)
  assert.match(adminSource, /activeUserManagementTab === 'personnel'/)
  assert.match(adminSource, /handlePersonnelManagementUpdated/)
  assert.match(adminSource, /@updated="handlePersonnelManagementUpdated"/)
  assert.match(adminSource, /await fetchUsers\(\)/)
})

test('AdminDashboard user table shows personnel info column', () => {
  assert.match(adminSource, /<th>\s*人员信息\s*<\/th>/)
  assert.match(adminSource, /personnel_display/)
})

test('AdminDashboard account list keeps actions in expandable rows without showing account id column', () => {
  assert.match(adminSource, /expandedUserActionIds/)
  assert.match(adminSource, /toggleUserActions\(user\.id\)/)
  assert.match(adminSource, /user-action-detail-row/)
  assert.match(adminSource, /colspan="8"/)
  assert.match(adminSource, />\s*用户名\s*</)
  assert.match(adminSource, />\s*角色\s*</)
  assert.match(adminSource, />\s*部门\s*</)
  assert.match(adminSource, />\s*人员信息\s*</)
  assert.match(adminSource, />\s*状态\s*</)
  assert.match(adminSource, />\s*创建时间\s*</)
  assert.doesNotMatch(adminSource, /<th>ID<\/th>/)
  assert.doesNotMatch(adminSource, /<th>操作<\/th>/)
})

test('AdminDashboard wires admin bind and unbind personnel actions', () => {
  assert.match(adminSource, /PersonnelLookupSelect/)
  assert.match(adminSource, /showPersonnelModal/)
  assert.match(adminSource, /loadPersonnelLookupOptions\(/)
  assert.match(adminSource, /initial-options=/)
  assert.match(adminSource, /bindUserPersonnel/)
  assert.match(adminSource, /unbindUserPersonnel/)
  assert.match(adminSource, /fetchUsers\(\)/)
})

test('quota management route redirects into admin quota tab', () => {
  assert.match(routerSource, /quota-management/)
  assert.match(routerSource, /tab:\s*['"]quota['"]/)
  assert.match(routerSource, /path:\s*['"]\/admin['"]/)
})
