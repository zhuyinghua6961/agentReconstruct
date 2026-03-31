import test from 'node:test'
import assert from 'node:assert/strict'

import {
  buildQuotaErrorCardModel,
  isQuotaBlockingErrorCode,
} from './quota-error-formatting.js'

test('isQuotaBlockingErrorCode matches quota exceeded and system blocking codes', () => {
  assert.equal(isQuotaBlockingErrorCode('QUOTA_EXCEEDED'), true)
  assert.equal(isQuotaBlockingErrorCode('QUOTA_CONFIG_MISSING'), true)
  assert.equal(isQuotaBlockingErrorCode('QUOTA_LOCK_TIMEOUT'), true)
  assert.equal(isQuotaBlockingErrorCode('OTHER_ERROR'), false)
})

test('buildQuotaErrorCardModel formats quota exceeded using top-level data', () => {
  const card = buildQuotaErrorCardModel({
    code: 'QUOTA_EXCEEDED',
    message: 'quota exceeded',
    data: {
      quota_type: 'file_qa',
      quota_name: '文件问答',
      current: 20,
      limit: 20,
      remaining: 0,
      reset_hint: 'next_day_start',
      windows: [
        {
          period: 'day',
          current: 20,
          limit: 20,
          remaining: 0,
          reset_hint: 'next_day_start',
        },
      ],
    },
    featureTitle: '文件问答',
  })

  assert.equal(card.variant, 'quota_exceeded')
  assert.equal(card.featureTitle, '文件问答')
  assert.equal(card.headline, '文件问答次数已用完')
  assert.equal(card.quotaType, 'file_qa')
  assert.equal(card.quotaName, '文件问答')
  assert.equal(card.usageSummary, '已用 20 / 20，剩余 0')
  assert.equal(card.resetText, '今日24:00')
  assert.equal(card.windows.length, 1)
  assert.equal(card.action.to, '/profile')
})

test('buildQuotaErrorCardModel maps system-side quota failures to system_unavailable', () => {
  const card = buildQuotaErrorCardModel({
    code: 'QUOTA_INTERNAL_UNAVAILABLE',
    message: 'service unavailable',
    data: {
      quota_type: 'ask_query',
      quota_name: '普通问答',
    },
    featureTitle: '普通问答',
  })

  assert.equal(card.variant, 'system_unavailable')
  assert.equal(card.headline, '普通问答暂不可用')
  assert.equal(card.description, '当前配额服务未就绪')
  assert.equal(card.usageSummary, '')
  assert.deepEqual(card.windows, [])
})

test('buildQuotaErrorCardModel falls back when quota detail is missing', () => {
  const card = buildQuotaErrorCardModel({
    code: 'QUOTA_EXCEEDED',
    message: 'quota exceeded',
    featureTitle: '普通问答',
  })

  assert.equal(card.variant, 'quota_exceeded')
  assert.equal(card.featureTitle, '普通问答')
  assert.equal(card.quotaType, '')
  assert.equal(card.quotaName, '')
  assert.equal(card.usageSummary, '')
  assert.equal(card.description, '')
})
