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

const adminSource = readSource(['AdminDashboard.vue'])
const batchImportSource = readSource(['..', 'components', 'BatchImportDialog.vue'])
const departmentBatchImportSource = readSource(['..', 'components', 'DepartmentBatchImportDialog.vue'])
const departmentCreateSource = readSource(['..', 'components', 'DepartmentCreateDialog.vue'])
const departmentImportResultSource = readSource(['..', 'components', 'DepartmentImportResultDialog.vue'])
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
  assert.match(batchImportSource, /username、password、user_type/)
  assert.match(batchImportSource, /部门信息由绑定的人员记录同步|不再从用户导入模板填写部门/)
  assert.doesNotMatch(batchImportSource, /primary_department_name/)
  assert.doesNotMatch(batchImportSource, /secondary_department_name/)
})

test('admin service exposes department import APIs', () => {
  assert.match(adminServiceSource, /batchImportDepartments/)
  assert.match(adminServiceSource, /downloadDepartmentImportTemplate/)
})

test('DepartmentBatchImportDialog documents status columns', () => {
  assert.match(departmentBatchImportSource, /primary_status/)
  assert.match(departmentBatchImportSource, /secondary_status/)
  assert.match(departmentBatchImportSource, /tertiary_status/)
  assert.match(departmentBatchImportSource, /active/)
  assert.match(departmentBatchImportSource, /disabled/)
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

test('DepartmentManagementPanel exposes delete actions instead of department status toggles', () => {
  assert.match(adminServiceSource, /deletePrimaryDepartment/)
  assert.match(adminServiceSource, /deleteSecondaryDepartment/)
  assert.match(adminServiceSource, /deleteTertiaryDepartment/)
  assert.match(panelSource, /handleDeletePrimary/)
  assert.match(panelSource, /handleDeleteSecondary/)
  assert.match(panelSource, /handleDeleteTertiary/)
  assert.match(panelSource, />删除</)
  assert.doesNotMatch(panelSource, /handleTogglePrimaryStatus/)
  assert.doesNotMatch(panelSource, /handleToggleSecondaryStatus/)
  assert.doesNotMatch(panelSource, /handleToggleTertiaryStatus/)
  assert.doesNotMatch(panelSource, /updatePrimaryDepartmentStatus/)
  assert.doesNotMatch(panelSource, /updateSecondaryDepartmentStatus/)
  assert.doesNotMatch(panelSource, /updateTertiaryDepartmentStatus/)
  assert.doesNotMatch(adminServiceSource, /updatePrimaryDepartmentStatus/)
  assert.doesNotMatch(adminServiceSource, /updateSecondaryDepartmentStatus/)
  assert.doesNotMatch(adminServiceSource, /updateTertiaryDepartmentStatus/)
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

test('admin service exposes tertiary and legacy department user query api', () => {
  assert.match(adminServiceSource, /getTertiaryDepartmentUsers/)
  assert.match(adminServiceSource, /departments\/tertiary\/\$\{tertiaryId\}\/users/)
  assert.match(adminServiceSource, /getSecondaryLegacyDepartmentUsers/)
  assert.match(adminServiceSource, /legacy-users/)
})

test('DepartmentManagementPanel renders collapsible secondary and tertiary sections with user counts', () => {
  assert.match(panelSource, /expandedSecondaryIds/)
  assert.match(panelSource, /toggleSecondary/)
  assert.match(panelSource, /isSecondaryExpanded/)
  assert.match(panelSource, /secondary\.tertiary_count/)
  assert.match(panelSource, /legacy_user_count/)
  assert.match(panelSource, /未补全三级部门用户/)
  assert.match(panelSource, /tertiary\.user_count/)
})

test('DepartmentManagementPanel lazy loads tertiary and legacy users with local loading and error states', () => {
  assert.match(panelSource, /createDepartmentUsersRuntime/)
  assert.match(panelSource, /loadDepartmentUsers/)
  assert.match(panelSource, /departmentUsersById/)
  assert.match(panelSource, /departmentUsersLoadingById/)
  assert.match(panelSource, /departmentUsersErrorById/)
  assert.match(runtimeSource, /获取用户列表失败/)
  assert.match(panelSource, /暂无用户/)
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
