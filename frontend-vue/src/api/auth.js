import { getJson, postJson, putJson } from './http';

const API_PREFIX = '/api/v1/auth';

export async function loginAuth(username, password) {
  return await postJson(
    `${API_PREFIX}/login`,
    { username, password },
    { auth: false }
  );
}

export async function registerAuth(username, password) {
  return await postJson(
    `${API_PREFIX}/register`,
    { username, password },
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

