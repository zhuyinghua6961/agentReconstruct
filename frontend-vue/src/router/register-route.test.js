import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const currentDir = dirname(fileURLToPath(import.meta.url))
const routerSource = readFileSync(join(currentDir, 'index.js'), 'utf8')
const loginSource = readFileSync(join(currentDir, '../views/Login.vue'), 'utf8')

test('router includes /register route', () => {
  assert.match(routerSource, /path:\s*'\/register'/)
})

test('Login shows a register account entry that routes to /register', () => {
  assert.match(loginSource, /注册账号/)
  assert.match(loginSource, /href="\/register"|to="\/register"|window\.location\.href = '\/register'/)
})

test('Login password input supports visibility toggle', () => {
  assert.match(loginSource, /showLoginPassword/)
  assert.match(loginSource, /password-toggle/)
  assert.match(loginSource, /:type="showLoginPassword \? 'text' : 'password'"/)
  assert.match(loginSource, /aria-label="显示或隐藏密码"/)
})

test('router treats /register as guest-facing and redirects authenticated users away', () => {
  assert.match(routerSource, /to\.path === '\/register'/)
  assert.match(routerSource, /currentUser\?\.role === 'admin' \? '\/admin' : '\/'/)
})

test('router validates token for /register via the same branch as /login before redirecting', () => {
  assert.match(routerSource, /to\.meta\.requiresAuth \|\| to\.path === '\/login' \|\| to\.path === '\/register'/)
  assert.match(routerSource, /authApi\.getMe\(\)/)
})
