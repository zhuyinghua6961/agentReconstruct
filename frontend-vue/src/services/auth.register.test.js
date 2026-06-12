import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { registerAuth } from '../api/auth.js'
import { authApi } from './auth.js'

const currentDir = dirname(fileURLToPath(import.meta.url))
const apiAuthSource = readFileSync(join(currentDir, '../api/auth.js'), 'utf8')
const apiServiceSource = readFileSync(join(currentDir, 'api.js'), 'utf8')
const adminServiceSource = readFileSync(join(currentDir, 'admin.js'), 'utf8')
const authServiceSource = readFileSync(join(currentDir, 'auth.js'), 'utf8')
const sessionSource = readFileSync(join(currentDir, '../features/auth/composables/useAuthSession.js'), 'utf8')

function createStorage(initial = {}) {
  const data = new Map(Object.entries(initial))
  return {
    getItem(key) {
      return data.has(key) ? data.get(key) : null
    },
    setItem(key, value) {
      data.set(key, String(value))
    },
    removeItem(key) {
      data.delete(key)
    },
  }
}

test('authApi.register posts the self-service registration payload without department fields', async () => {
  const originalFetch = global.fetch

  const calls = []
  global.fetch = async (url, options = {}) => {
    calls.push([url, options])
    return {
      ok: true,
      async json() {
        return { success: true, data: { token: 'token-1' } }
      },
    }
  }

  try {
    await authApi.register({
      username: 'alice',
      password: 'Secret123!',
      confirmPassword: 'Secret123!',
      employee_no: 'T2024001',
      full_name: '张三',
      verification_code: 'ABC123',
      security_questions: [
        { question: '我最喜欢的水果是什么？', answer: '苹果' },
      ],
    })

    assert.equal(calls.length, 1)
    const [url, options] = calls[0]
    assert.equal(url, '/api/auth/register')
    assert.equal(options.method, 'POST')
    assert.equal(options.headers['Content-Type'], 'application/json')

    const body = JSON.parse(options.body)
    assert.equal(body.username, 'alice')
    assert.equal(body.password, 'Secret123!')
    assert.equal(body.employee_no, 'T2024001')
    assert.equal(body.full_name, '张三')
    assert.equal(body.verification_code, 'ABC123')
    assert.deepEqual(body.security_questions, [
      { question: '我最喜欢的水果是什么？', answer: '苹果' },
    ])
    assert.equal('confirmPassword' in body, false)
    assert.equal('confirm_password' in body, false)
    assert.equal('primary_department_id' in body, false)
    assert.equal('secondary_department_id' in body, false)
    assert.equal('tertiary_department_id' in body, false)
  } finally {
    global.fetch = originalFetch
  }
})

test('authApi.register converts non-json error responses into structured failure payload', async () => {
  const originalFetch = global.fetch
  const originalLocalStorage = global.localStorage
  const originalWindow = global.window

  global.localStorage = createStorage()
  global.window = { location: { pathname: '/register', href: '/register' } }
  global.fetch = async () => ({
    ok: false,
    status: 502,
    async json() {
      throw new Error('bad json')
    },
  })

  try {
    const result = await authApi.register({
      username: 'alice',
      password: 'Secret123!',
      employee_no: 'T2024001',
      full_name: '张三',
      verification_code: 'ABC123',
      security_questions: [{ question: '我最喜欢的水果是什么？', answer: '苹果' }],
    })
    assert.equal(result.success, false)
    assert.equal(result.error, 'HTTP 502')
  } finally {
    global.fetch = originalFetch
    global.localStorage = originalLocalStorage
    global.window = originalWindow
  }
})

test('auxiliary auth helpers no longer expose a username-password-only register signature', async () => {
  assert.match(apiAuthSource, /registerAuth\(payload\)/)
  assert.doesNotMatch(apiAuthSource, /registerAuth\(username,\s*password\)/)
  assert.match(sessionSource, /async function register\(payload\)/)
  assert.match(sessionSource, /registerAuth\(payload\)/)
  assert.doesNotMatch(sessionSource, /registerAuth\(username,\s*password\)/)
})

test('global service error handlers handle disabled personnel with personnel details', () => {
  for (const source of [apiServiceSource, adminServiceSource, authServiceSource]) {
    assert.match(source, /PERSONNEL_DISABLED/)
    assert.match(source, /账号所属人员已停用，请联系管理员/)
    assert.match(source, /employee_no/)
    assert.match(source, /full_name/)
    assert.match(source, /department_display/)
    assert.match(source, /clearStoredAuth\(\)/)
    assert.match(source, /window\.location\.href = '\/login'/)
  }
})

test('global service error handlers handle disabled departments with personnel details', () => {
  for (const source of [apiServiceSource, adminServiceSource, authServiceSource]) {
    assert.match(source, /DEPARTMENT_DISABLED/)
    assert.match(source, /账号所属部门已停用，请联系管理员/)
    assert.match(source, /employee_no/)
    assert.match(source, /full_name/)
    assert.match(source, /department_display/)
    assert.match(source, /clearStoredAuth\(\)/)
    assert.match(source, /window\.location\.href = '\/login'/)
  }
})

test('registerAuth strips confirmPassword fields before posting to backend', async () => {
  const originalFetch = global.fetch

  const calls = []
  global.fetch = async (url, options = {}) => {
    calls.push([url, options])
    return {
      ok: true,
      async json() {
        return { success: true, data: { token: 'token-1' } }
      },
    }
  }

  try {
    await registerAuth({
      username: 'alice',
      password: 'Secret123!',
      confirmPassword: 'Secret123!',
      confirm_password: 'Secret123!',
      employee_no: 'T2024001',
      full_name: '张三',
      verification_code: 'ABC123',
      security_questions: [
        { question: '我最喜欢的水果是什么？', answer: '苹果' },
      ],
    })

    assert.equal(calls.length, 1)
    const [, options] = calls[0]
    const body = JSON.parse(options.body)
    assert.equal('confirmPassword' in body, false)
    assert.equal('confirm_password' in body, false)
    assert.equal('primary_department_id' in body, false)
    assert.equal('secondary_department_id' in body, false)
    assert.equal('tertiary_department_id' in body, false)
    assert.deepEqual(body.security_questions, [
      { question: '我最喜欢的水果是什么？', answer: '苹果' },
    ])
  } finally {
    global.fetch = originalFetch
  }
})
