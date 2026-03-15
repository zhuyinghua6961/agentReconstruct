import {
  createQuotaConfig as requestCreateQuotaConfig,
  getMyQuotas as requestMyQuotas,
  getQuotaConfigs as requestQuotaConfigs,
  getUserQuotas as requestUserQuotas,
  resetUserQuota as requestResetUserQuota,
  updateQuotaConfig as requestUpdateQuotaConfig,
} from '../api/quota'

function formatResetTime(resetHint) {
  const hint = String(resetHint || '').trim()
  if (hint === 'next_day_start') return '今日24:00'
  if (hint === 'next_week_start') return '下周开始'
  if (hint === 'next_month_start') return '下月1号00:00'
  if (hint.startsWith('next_custom_window_start:')) {
    const value = hint.split(':', 2)[1] || ''
    return value ? `${value} 00:00` : '自定义窗口重置'
  }
  if (hint === 'never') return '无限制'
  return hint || '未知'
}

function normalizeWindows(rawWindows, fallbackQuota = null) {
  const items = Array.isArray(rawWindows) ? rawWindows : []
  const normalized = []
  for (const item of items) {
    const period = String(item?.period || '').trim()
    if (!period) continue
    normalized.push({
      period,
      period_days: Number(item?.period_days || 0),
      current: Number(item?.current || 0),
      limit: Number(item?.limit || 0),
      remaining: Number(item?.remaining || 0),
      reset_time: formatResetTime(item?.reset_hint),
      allowed: item?.allowed !== false,
    })
  }
  if (normalized.length > 0) {
    return normalized
  }

  if (!fallbackQuota || !fallbackQuota.period) {
    return []
  }
  return [
    {
      period: String(fallbackQuota.period || 'none'),
      period_days: Number(fallbackQuota.period_days || 0),
      current: Number(fallbackQuota.current || 0),
      limit: Number(fallbackQuota.limit || 0),
      remaining: Number(fallbackQuota.remaining || 0),
      reset_time: formatResetTime(fallbackQuota.reset_hint),
      allowed: true,
    },
  ]
}

function normalizeMyQuotaData(rawData) {
  if (!rawData || typeof rawData !== 'object') {
    return {}
  }

  // 已经是对象映射结构，直接返回
  if (!Array.isArray(rawData.quotas)) {
    return rawData
  }

  const normalized = {}
  for (const item of rawData.quotas) {
    const quotaType = String(item?.quota_type || '').trim()
    if (!quotaType) continue
    const windows = normalizeWindows(item?.windows, item)
    normalized[quotaType] = {
      name: item?.quota_name || quotaType,
      period: item?.period || 'none',
      period_days: Number(item?.period_days || 0),
      current: Number(item?.current || 0),
      limit: Number(item?.limit || 0),
      remaining: Number(item?.remaining || 0),
      reset_time: formatResetTime(item?.reset_hint),
      windows,
    }
  }
  return normalized
}

// 配额API
export const quotaApi = {
  // 获取当前用户配额
  async getMyQuotas() {
    try {
      const result = await requestMyQuotas()
      if (result?.success) {
        return {
          ...result,
          data: normalizeMyQuotaData(result.data),
        }
      }
      return result
    } catch (error) {
      console.error('获取配额失败:', error)
      return {
        success: false,
        error: '获取配额失败'
      }
    }
  },

  // 获取配额配置（管理员）
  async getQuotaConfigs() {
    try {
      return await requestQuotaConfigs()
    } catch (error) {
      console.error('获取配额配置失败:', error)
      return {
        success: false,
        error: '获取配额配置失败'
      }
    }
  },

  // 新增配额配置（管理员）
  async createQuotaConfig(config) {
    try {
      return await requestCreateQuotaConfig(config)
    } catch (error) {
      console.error('新增配额配置失败:', error)
      return {
        success: false,
        error: '新增配额配置失败'
      }
    }
  },

  // 更新配额配置（管理员）
  async updateQuotaConfig(quotaType, config) {
    try {
      return await requestUpdateQuotaConfig(quotaType, config)
    } catch (error) {
      console.error('更新配额配置失败:', error)
      return {
        success: false,
        error: '更新配额配置失败'
      }
    }
  },

  // 重置用户配额（管理员）
  async resetUserQuota(userId, quotaType) {
    try {
      return await requestResetUserQuota(userId, quotaType)
    } catch (error) {
      console.error('重置配额失败:', error)
      return {
        success: false,
        error: '重置配额失败'
      }
    }
  },

  // 获取指定用户配额（管理员）
  async getUserQuotas(userId) {
    try {
      return await requestUserQuotas(userId)
    } catch (error) {
      console.error('获取用户配额失败:', error)
      return {
        success: false,
        error: '获取用户配额失败'
      }
    }
  }
}
