import { getJson, postJson, putJson } from './http';

const API_PREFIX = '/api/v1/quota';

export async function getMyQuotas() {
  return await getJson(`${API_PREFIX}/my`);
}

export async function getQuotaConfigs() {
  return await getJson(`${API_PREFIX}/configs`);
}

export async function createQuotaConfig(payload) {
  return await postJson(`${API_PREFIX}/configs`, payload || {});
}

export async function updateQuotaConfig(quotaType, payload) {
  return await putJson(`${API_PREFIX}/configs/${encodeURIComponent(quotaType)}`, payload || {});
}

export async function getUserQuotas(userId) {
  return await getJson(`${API_PREFIX}/users/${encodeURIComponent(userId)}`);
}

export async function resetUserQuota(userId, quotaType) {
  return await postJson(
    `${API_PREFIX}/reset/${encodeURIComponent(userId)}/${encodeURIComponent(quotaType)}`,
    {}
  );
}
