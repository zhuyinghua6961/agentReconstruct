import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const currentDir = dirname(fileURLToPath(import.meta.url))
const loginSource = readFileSync(join(currentDir, 'Login.vue'), 'utf8')
const profileSource = readFileSync(join(currentDir, 'UserProfile.vue'), 'utf8')

test('Login persists department completion flags from the auth payload', () => {
  assert.match(loginSource, /require_department_setup/)
  assert.match(loginSource, /result\.require_department_setup|result\.data\?\.require_department_setup/)
})

test('UserProfile renders department completion card and selector', () => {
  assert.match(profileSource, /部门信息/)
  assert.match(profileSource, /DepartmentSelector/)
  assert.match(profileSource, /selectedTertiaryDepartmentId/)
  assert.match(profileSource, /tertiary-id/)
})

test('UserProfile keeps department fetch errors scoped to the department section', () => {
  assert.match(profileSource, /departmentError/)
  assert.match(profileSource, /departmentSuccess/)
})

test('UserProfile exposes username edit flow for non-admin users', () => {
  assert.match(profileSource, /修改用户名/)
  assert.match(profileSource, /authApi\.updateUsername/)
  assert.match(profileSource, /function isAdminIdentity|const isAdminIdentity/)
  assert.match(profileSource, /user_type === 1|role === 'admin'/)
  assert.match(profileSource, /syncStoredUser\(/)
})

test('UserProfile resets username draft from current user when opening and cancelling edit', () => {
  assert.match(profileSource, /function openUsernameForm|const openUsernameForm/)
  assert.match(profileSource, /function cancelUsernameEdit|const cancelUsernameEdit/)
  assert.match(profileSource, /usernameInput\.value = currentUser\.value\?\.username \|\| ''/)
  assert.match(profileSource, /usernameError\.value = ''/)
  assert.match(profileSource, /usernameSuccess\.value = ''/)
  assert.match(profileSource, /@click="openUsernameForm"/)
  assert.match(profileSource, /@click="cancelUsernameEdit"/)
})
