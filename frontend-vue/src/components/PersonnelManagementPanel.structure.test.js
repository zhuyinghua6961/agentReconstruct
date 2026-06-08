import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const currentDir = dirname(fileURLToPath(import.meta.url))

function readSource(filename) {
  const fullPath = join(currentDir, filename)
  if (!existsSync(fullPath)) {
    return ''
  }
  return readFileSync(fullPath, 'utf8')
}

const panelSource = readSource('PersonnelManagementPanel.vue')
const editorSource = readSource('PersonnelEditorDialog.vue')
const lookupSource = readSource('PersonnelLookupSelect.vue')
const batchImportSource = readSource('PersonnelBatchImportDialog.vue')
const importResultSource = readSource('PersonnelImportResultDialog.vue')
const adminServiceSource = readSource('../services/admin.js')

test('PersonnelManagementPanel renders account list filters and status filter', () => {
  assert.match(panelSource, /searchEmployeeNo/)
  assert.match(panelSource, /searchFullName/)
  assert.match(panelSource, /statusFilter/)
  assert.match(panelSource, /工号/)
  assert.match(panelSource, /姓名/)
  assert.match(panelSource, /状态/)
})

test('PersonnelEditorDialog reuses DepartmentSelector with searchable department selection', () => {
  assert.match(editorSource, /DepartmentSelector/)
  assert.match(editorSource, /searchPlaceholder/)
  assert.match(editorSource, /一级部门必选，二级和三级部门可按实际管理层级留空/)
  assert.match(editorSource, /primary_department_id/)
  assert.match(editorSource, /secondary_department_id/)
  assert.match(editorSource, /tertiary_department_id/)
})

test('PersonnelManagementPanel exposes create edit status and bindings actions', () => {
  assert.match(panelSource, /PersonnelEditorDialog/)
  assert.match(panelSource, /openCreateDialog/)
  assert.match(panelSource, /openEditDialog/)
  assert.match(panelSource, /handleTogglePersonnelStatus/)
  assert.match(panelSource, /handleDeletePersonnel/)
  assert.match(panelSource, /toggleBindings/)
  assert.match(panelSource, /绑定账号数/)
  assert.match(panelSource, />删除</)
})

test('PersonnelManagementPanel aligns personnel table controls with account list styling', () => {
  assert.match(panelSource, /getPersonnelStatusText/)
  assert.match(panelSource, /status-badge/)
  assert.match(panelSource, /class="action-btn"/)
  assert.match(panelSource, /\.personnel-table th\s*\{[^}]*background:\s*#f9fafb/s)
  assert.match(panelSource, /\.action-btn\s*\{[^}]*border:\s*1px solid #d1d5db/s)
  assert.doesNotMatch(panelSource, /class="link-btn"/)
})

test('PersonnelManagementPanel submits primary secondary tertiary department ids on create and update', () => {
  assert.match(panelSource, /primary_department_id/)
  assert.match(panelSource, /secondary_department_id/)
  assert.match(panelSource, /tertiary_department_id/)
  assert.match(panelSource, /status:\s*normalizedPayload\.status/)
  assert.doesNotMatch(panelSource, /updatePersonnelStatus\(currentItem\.id,\s*normalizedPayload\.status\)/)
  assert.doesNotMatch(panelSource, /window\.prompt/)
})

test('PersonnelManagementPanel wires batch import and template download', () => {
  assert.match(panelSource, /PersonnelBatchImportDialog/)
  assert.match(panelSource, /PersonnelImportResultDialog/)
  assert.match(panelSource, /downloadPersonnelImportTemplate/)
  assert.match(panelSource, /handlePersonnelImportSuccess/)
  assert.match(batchImportSource, /工号/)
  assert.match(batchImportSource, /姓名/)
  assert.match(batchImportSource, /一级部门/)
  assert.match(batchImportSource, /二级部门/)
  assert.match(batchImportSource, /三级部门/)
  assert.match(batchImportSource, /校验码/)
  assert.match(batchImportSource, /兼容旧英文列名/)
  assert.match(batchImportSource, /工号作为匹配键/)
  assert.match(batchImportSource, /一级部门必填/)
  assert.match(batchImportSource, /二级、三级部门可留空/)
  assert.match(batchImportSource, /内容一致会显示跳过/)
  assert.match(batchImportSource, /其他信息变化会显示更新/)
  assert.match(batchImportSource, /工号变化会作为新人员创建/)
  assert.doesNotMatch(batchImportSource, /数据库里已存在的工号会按导入值覆盖更新/)
  assert.doesNotMatch(batchImportSource, /employee_no、full_name、verification_code、status/)
  assert.match(importResultSource, /工号/)
  assert.match(importResultSource, /姓名/)
})

test('PersonnelManagementPanel shows personnel department display in list rows', () => {
  assert.match(panelSource, /department_display/)
  assert.match(panelSource, /部门/)
})

test('Personnel import result dialog supports created updated summary and statuses', () => {
  assert.match(importResultSource, /getPersonnelImportSuccessCount/)
  assert.match(importResultSource, /filterPersonnelImportDetails/)
  assert.match(importResultSource, /getPersonnelImportResultText/)
  assert.match(importResultSource, /一级部门|department_display/)
  assert.match(importResultSource, /二级部门|secondary_department_name/)
  assert.match(importResultSource, /三级部门|tertiary_department_name/)
  assert.match(importResultSource, /自动创建部门/)
  assert.match(importResultSource, /created_departments_total/)
})

test('Personnel batch import documents automatic department creation behavior', () => {
  assert.match(batchImportSource, /部门不存在时会自动创建/)
  assert.match(batchImportSource, /停用部门不会自动启用/)
})

test('PersonnelManagementPanel lazy loads bindings when expanding a personnel row', () => {
  assert.match(panelSource, /expandedPersonnelIds/)
  assert.match(panelSource, /bindingsByPersonnelId/)
  assert.match(panelSource, /bindingsLoadingByPersonnelId/)
  assert.match(panelSource, /bindingsErrorByPersonnelId/)
  assert.match(panelSource, /getPersonnelBindings/)
})

test('PersonnelLookupSelect supports keyword search and active-only selection', () => {
  assert.match(lookupSource, /keyword/)
  assert.match(lookupSource, /fetchOptions\(nextKeyword = keyword\.value, \{ pageSize = 20/)
  assert.match(lookupSource, /getPersonnel\(\{\s*keyword:\s*searchKeyword,\s*status:\s*'active',\s*page_size:\s*pageSize\s*\}\)/)
  assert.match(lookupSource, /employee_no \/\ full_name|employee_no.*full_name/)
  assert.match(lookupSource, /isActiveOption/)
})

test('PersonnelLookupSelect supports parent-preloaded options and local keyword filtering', () => {
  assert.match(lookupSource, /initialOptions/)
  assert.match(lookupSource, /setOptions\(/)
  assert.match(lookupSource, /includes\(normalizedKeyword\)/)
  assert.match(lookupSource, /filteredOptions/)
})

test('admin service exposes personnel management APIs', () => {
  assert.match(adminServiceSource, /getPersonnel\(/)
  assert.match(adminServiceSource, /createPersonnel\(/)
  assert.match(adminServiceSource, /updatePersonnel\(/)
  assert.match(adminServiceSource, /updatePersonnelStatus\(/)
  assert.match(adminServiceSource, /deletePersonnel\(/)
  assert.match(adminServiceSource, /getPersonnelBindings\(/)
  assert.match(adminServiceSource, /batchImportPersonnel\(/)
  assert.match(adminServiceSource, /downloadPersonnelImportTemplate\(/)
  assert.match(adminServiceSource, /bindUserPersonnel\(/)
  assert.match(adminServiceSource, /unbindUserPersonnel\(/)
})
