export function isAdminUser(user) {
  return user?.role === 'admin' || Number(user?.user_type) === 1
}

export function hasRequiredProfileSetup(user) {
  return Boolean(
    user?.is_first_login
    || user?.require_security_questions_setup
    || (!isAdminUser(user) && user?.require_department_setup)
    || (!isAdminUser(user) && user?.require_personnel_setup)
  )
}

export function buildRequiredProfilePath(user) {
  const params = new URLSearchParams()
  if (user?.is_first_login) params.set('change_password', 'required')
  if (user?.require_security_questions_setup) params.set('security_questions', 'required')
  if (!isAdminUser(user) && user?.require_department_setup) params.set('department', 'required')
  if (!isAdminUser(user) && user?.require_personnel_setup) params.set('personnel', 'required')
  const query = params.toString()
  return query ? `/profile?${query}` : '/profile'
}

export function mergeValidatedUser(cachedUser, freshUser) {
  if (!freshUser) return cachedUser || null
  const mergedUser = {
    ...(cachedUser || {}),
    ...freshUser,
    is_first_login: Boolean(freshUser?.is_first_login),
    require_security_questions_setup: Boolean(freshUser?.require_security_questions_setup),
    require_department_setup: Boolean(freshUser?.require_department_setup),
    require_personnel_setup: Boolean(freshUser?.require_personnel_setup),
    has_security_questions: Boolean(freshUser?.has_security_questions),
  }
  if (isAdminUser(mergedUser)) {
    mergedUser.require_department_setup = false
    mergedUser.require_personnel_setup = false
  }
  return mergedUser
}
