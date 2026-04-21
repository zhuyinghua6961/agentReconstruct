import { getJson, postJson, putJson } from './http.js';

const API_PREFIX = '/api/auth';

function normalizeRegisterPayload(payload) {
  return {
    username: payload?.username ?? '',
    password: payload?.password ?? '',
    employee_no: payload?.employee_no ?? '',
    full_name: payload?.full_name ?? '',
    verification_code: payload?.verification_code ?? '',
    security_questions: Array.isArray(payload?.security_questions) ? payload.security_questions : [],
  };
}

export async function loginAuth(username, password) {
  return await postJson(
    `${API_PREFIX}/login`,
    { username, password },
    { auth: false }
  );
}

export async function registerAuth(payload) {
  return await postJson(
    `${API_PREFIX}/register`,
    normalizeRegisterPayload(payload),
    { auth: false }
  );
}

export async function getCurrentUser() {
  return await getJson(`${API_PREFIX}/me`);
}

export async function changePassword(oldPassword, newPassword) {
  return await putJson(`${API_PREFIX}/password`, {
    old_password: oldPassword,
    new_password: newPassword,
  });
}
