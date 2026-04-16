import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const currentDir = dirname(fileURLToPath(import.meta.url))
const adminSource = readFileSync(join(currentDir, 'AdminDashboard.vue'), 'utf8')
const batchImportSource = readFileSync(join(currentDir, '..', 'components', 'BatchImportDialog.vue'), 'utf8')
const adminServiceSource = readFileSync(join(currentDir, '..', 'services', 'admin.js'), 'utf8')

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
