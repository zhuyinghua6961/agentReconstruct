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
const departmentImportResultSource = readSource(['..', 'components', 'DepartmentImportResultDialog.vue'])
const panelSource = readSource(['..', 'components', 'DepartmentManagementPanel.vue'])
const adminServiceSource = readSource(['..', 'services', 'admin.js'])

test('AdminDashboard includes department management entry and user department column', () => {
  assert.match(adminSource, /DepartmentManagementPanel/)
  assert.match(adminSource, /部门管理/)
  assert.match(adminSource, /<th>\s*部门\s*<\/th>/)
})

test('AdminDashboard wires department assignment into user editing flows', () => {
  assert.match(adminSource, /DepartmentSelector/)
  assert.match(adminSource, /updateUserDepartment|getDepartmentTree/)
})

test('admin service exposes department dictionary and user department APIs', () => {
  assert.match(adminServiceSource, /getDepartmentTree/)
  assert.match(adminServiceSource, /updateUserDepartment/)
})

test('BatchImportDialog documents department name columns in the template', () => {
  assert.match(batchImportSource, /primary_department_name/)
  assert.match(batchImportSource, /secondary_department_name/)
})

test('admin service exposes department import APIs', () => {
  assert.match(adminServiceSource, /batchImportDepartments/)
  assert.match(adminServiceSource, /downloadDepartmentImportTemplate/)
})

test('DepartmentBatchImportDialog documents status columns', () => {
  assert.match(departmentBatchImportSource, /primary_status/)
  assert.match(departmentBatchImportSource, /secondary_status/)
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

test('Department import result dialog shows department columns', () => {
  assert.match(departmentImportResultSource, /一级部门/)
  assert.match(departmentImportResultSource, /一级状态/)
  assert.match(departmentImportResultSource, /二级部门/)
  assert.match(departmentImportResultSource, /二级状态/)
})
