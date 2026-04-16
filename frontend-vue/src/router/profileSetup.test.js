import test from 'node:test'
import assert from 'node:assert/strict'

import {
  buildRequiredProfilePath,
  hasRequiredProfileSetup,
  isAdminUser,
  mergeValidatedUser,
} from './profileSetup.js'

test('isAdminUser accepts admin role and admin user_type', () => {
  assert.equal(isAdminUser({ role: 'admin', user_type: 3 }), true)
  assert.equal(isAdminUser({ role: 'user', user_type: 1 }), true)
  assert.equal(isAdminUser({ role: 'user', user_type: 3 }), false)
})

test('hasRequiredProfileSetup ignores department completion for admins', () => {
  assert.equal(
    hasRequiredProfileSetup({
      role: 'admin',
      user_type: 1,
      require_department_setup: true,
    }),
    false,
  )
  assert.equal(
    hasRequiredProfileSetup({
      role: 'user',
      user_type: 3,
      require_department_setup: true,
    }),
    true,
  )
})

test('buildRequiredProfilePath only appends department for non-admin users', () => {
  assert.equal(
    buildRequiredProfilePath({
      role: 'admin',
      user_type: 1,
      require_department_setup: true,
    }),
    '/profile',
  )
  assert.equal(
    buildRequiredProfilePath({
      role: 'user',
      user_type: 3,
      require_department_setup: true,
    }),
    '/profile?department=required',
  )
})

test('mergeValidatedUser prefers fresh auth payload and clears admin department requirement', () => {
  const merged = mergeValidatedUser(
    {
      role: 'user',
      user_type: 3,
      require_department_setup: true,
      has_security_questions: false,
    },
    {
      role: 'admin',
      user_type: 1,
      require_department_setup: true,
      has_security_questions: true,
    },
  )

  assert.equal(merged.role, 'admin')
  assert.equal(merged.user_type, 1)
  assert.equal(merged.require_department_setup, false)
  assert.equal(merged.has_security_questions, true)
})
