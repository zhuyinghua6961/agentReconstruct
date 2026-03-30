import {
  createQuotaConfig as requestCreateQuotaConfig,
  getMyQuotas as requestMyQuotas,
  getQuotaConfigs as requestQuotaConfigs,
  getUserQuotas as requestUserQuotas,
  resetUserQuota as requestResetUserQuota,
  updateQuotaConfig as requestUpdateQuotaConfig,
} from '../api/quota'
import {
  normalizeMyQuotaData,
  normalizeQuotaConfigList,
  normalizeUserQuotaList,
} from './quota-normalization'

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
      const result = await requestQuotaConfigs()
      if (result?.success) {
        return {
          ...result,
          data: {
            ...(result.data || {}),
            configs: normalizeQuotaConfigList(result?.data?.configs || []),
          },
        }
      }
      return result
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
      const result = await requestUserQuotas(userId)
      if (result?.success) {
        return {
          ...result,
          data: {
            ...(result.data || {}),
            quotas: normalizeUserQuotaList(result?.data?.quotas || []),
          },
        }
      }
      return result
    } catch (error) {
      console.error('获取用户配额失败:', error)
      return {
        success: false,
        error: '获取用户配额失败'
      }
    }
  }
}
