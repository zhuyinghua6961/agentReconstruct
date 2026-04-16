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
})

test('UserProfile keeps department fetch errors scoped to the department section', () => {
  assert.match(profileSource, /departmentError/)
  assert.match(profileSource, /departmentSuccess/)
})
