import { computed, ref } from 'vue';
import { quotaApi } from '../../../services/quota';

function toBool(value) {
  if (typeof value === 'boolean') {
    return value;
  }
  const text = String(value ?? '').trim().toLowerCase();
  return text === '1' || text === 'true' || text === 'yes' || text === 'on';
}

function toInt(value, fallback = 0) {
  const num = Number.parseInt(String(value ?? ''), 10);
  return Number.isFinite(num) ? num : fallback;
}

function normalizeConfigs(items) {
  if (!Array.isArray(items)) {
    return [];
  }
  return items.map((item) => ({
    ...item,
    editDefaultLimit: toInt(item?.default_limit, 0),
    editIsActive: toBool(item?.is_active),
  }));
}

export function useQuotaAdmin({ authUserRef, isAuthenticatedRef }) {
  const loading = ref(false);
  const error = ref('');
  const quotaConfigs = ref([]);
  const targetUserId = ref('');
  const targetUserQuotas = ref([]);

  const isAdmin = computed(
    () => Boolean(isAuthenticatedRef?.value && String(authUserRef?.value?.role || '') === 'admin')
  );

  function clearState() {
    error.value = '';
    quotaConfigs.value = [];
    targetUserId.value = '';
    targetUserQuotas.value = [];
  }

  async function refreshQuotaConfigs() {
    if (!isAdmin.value) {
      clearState();
      return;
    }
    loading.value = true;
    error.value = '';
    try {
      const resp = await quotaApi.getQuotaConfigs();
      quotaConfigs.value = normalizeConfigs(resp?.data?.configs || []);
    } catch (err) {
      error.value = `加载配额配置失败: ${String(err)}`;
      quotaConfigs.value = [];
    } finally {
      loading.value = false;
    }
  }

  function setTargetUserId(value) {
    targetUserId.value = String(value ?? '').replace(/[^\d]/g, '');
  }

  function updateQuotaDraft({ quotaType, defaultLimit, isActive }) {
    const idx = quotaConfigs.value.findIndex((item) => String(item?.quota_type) === String(quotaType));
    if (idx < 0) {
      return;
    }
    if (defaultLimit !== undefined) {
      quotaConfigs.value[idx].editDefaultLimit = toInt(defaultLimit, quotaConfigs.value[idx].editDefaultLimit);
    }
    if (isActive !== undefined) {
      quotaConfigs.value[idx].editIsActive = Boolean(isActive);
    }
  }

  async function saveQuotaConfig(quotaType) {
    if (!isAdmin.value) {
      return false;
    }
    const row = quotaConfigs.value.find((item) => String(item?.quota_type) === String(quotaType));
    if (!row) {
      return false;
    }
    loading.value = true;
    error.value = '';
    try {
      const resp = await quotaApi.updateQuotaConfig(row.quota_type, {
        default_limit: toInt(row.editDefaultLimit, 0),
        is_active: Boolean(row.editIsActive),
      });
      if (!resp?.success) {
        error.value = String(resp?.error || '保存失败');
        return false;
      }
      await refreshQuotaConfigs();
      if (targetUserId.value) {
        await loadTargetUserQuotas();
      }
      return true;
    } catch (err) {
      error.value = `保存配额配置失败: ${String(err)}`;
      return false;
    } finally {
      loading.value = false;
    }
  }

  async function loadTargetUserQuotas() {
    if (!isAdmin.value) {
      return false;
    }
    const userId = toInt(targetUserId.value, 0);
    if (userId <= 0) {
      error.value = '请输入有效用户 ID';
      targetUserQuotas.value = [];
      return false;
    }
    loading.value = true;
    error.value = '';
    try {
      const resp = await quotaApi.getUserQuotas(userId);
      if (!resp?.success) {
        error.value = String(resp?.error || '查询用户配额失败');
        targetUserQuotas.value = [];
        return false;
      }
      targetUserQuotas.value = Array.isArray(resp?.data?.quotas) ? resp.data.quotas : [];
      return true;
    } catch (err) {
      error.value = `查询用户配额失败: ${String(err)}`;
      targetUserQuotas.value = [];
      return false;
    } finally {
      loading.value = false;
    }
  }

  async function resetTargetUserQuota(quotaType) {
    if (!isAdmin.value) {
      return false;
    }
    const userId = toInt(targetUserId.value, 0);
    if (userId <= 0) {
      error.value = '请输入有效用户 ID';
      return false;
    }
    loading.value = true;
    error.value = '';
    try {
      const resp = await quotaApi.resetUserQuota(userId, quotaType);
      if (!resp?.success) {
        error.value = String(resp?.error || '重置失败');
        return false;
      }
      await loadTargetUserQuotas();
      return true;
    } catch (err) {
      error.value = `重置用户配额失败: ${String(err)}`;
      return false;
    } finally {
      loading.value = false;
    }
  }

  return {
    isAdmin,
    loading,
    error,
    quotaConfigs,
    targetUserId,
    targetUserQuotas,
    clearState,
    refreshQuotaConfigs,
    setTargetUserId,
    updateQuotaDraft,
    saveQuotaConfig,
    loadTargetUserQuotas,
    resetTargetUserQuota,
  };
}
