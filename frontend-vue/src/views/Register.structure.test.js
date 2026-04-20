import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const currentDir = dirname(fileURLToPath(import.meta.url))
const registerPath = join(currentDir, 'Register.vue')
const registerSource = existsSync(registerPath) ? readFileSync(registerPath, 'utf8') : ''

test('Register renders account department personnel and security question sections', () => {
  assert.match(registerSource, /注册账号/)
  assert.match(registerSource, /用户名/)
  assert.match(registerSource, /密码/)
  assert.match(registerSource, /确认密码/)
  assert.match(registerSource, /部门信息/)
  assert.match(registerSource, /人员信息/)
  assert.match(registerSource, /安全问题设置|安全问题/)
})

test('Register reuses DepartmentSelector and authApi.register', () => {
  assert.match(registerSource, /DepartmentSelector/)
  assert.match(registerSource, /authApi\.register/)
  assert.match(registerSource, /selectedPrimaryDepartmentId/)
  assert.match(registerSource, /selectedSecondaryDepartmentId/)
  assert.match(registerSource, /selectedTertiaryDepartmentId/)
})

test('Register includes password confirmation and preset security question workflow', () => {
  assert.match(registerSource, /confirmPassword/)
  assert.match(registerSource, /presetQuestions/)
  assert.match(registerSource, /securityQuestions/)
  assert.match(registerSource, /securityAnswers/)
  assert.match(registerSource, /addQuestion/)
})
