import test from 'node:test'
import assert from 'node:assert/strict'

import {
  CANONICAL_QUOTA_TYPES,
  normalizeMyQuotaData,
  normalizeQuotaConfigList,
  normalizeUserQuotaList,
} from '../src/services/quota-normalization.js'

test('normalizeQuotaConfigList keeps only canonical buckets in fixed order', () => {
  const items = [
    { quota_type: 'text_translate', quota_name: '旧翻译', default_limit: 3, is_active: true },
    { quota_type: 'file_view', quota_name: '查看原文', default_limit: 10, is_active: true },
    { quota_type: 'ask_query', quota_name: '普通问答', default_limit: 20, is_active: true },
    { quota_type: 'hybrid_qa', quota_name: '旧混合问答', default_limit: 8, is_active: true },
    { quota_type: 'doc_assist', quota_name: '文档辅助', default_limit: 5, is_active: true },
  ]

  const normalized = normalizeQuotaConfigList(items)

  assert.deepEqual(
    normalized.map((item) => item.quota_type),
    ['ask_query', 'file_qa', 'file_view', 'doc_assist'],
  )
  assert.equal(normalized.find((item) => item.quota_type === 'file_qa')?.default_limit, 8)
  assert.equal(normalized.find((item) => item.quota_type === 'doc_assist')?.quota_name, '文档辅助')
})

test('normalizeUserQuotaList maps legacy aliases into canonical user-visible buckets', () => {
  const items = [
    { quota_type: 'pdf_summary', quota_name: '旧总结', current: 1, limit: 5, remaining: 4 },
    { quota_type: 'ask_query', quota_name: '普通问答', current: 2, limit: 10, remaining: 8 },
    { quota_type: 'tabular_qa', quota_name: '旧表格问答', current: 3, limit: 6, remaining: 3 },
    { quota_type: 'unknown_quota', quota_name: '未知类型', current: 1, limit: 1, remaining: 0 },
  ]

  const normalized = normalizeUserQuotaList(items)

  assert.deepEqual(
    normalized.map((item) => item.quota_type),
    ['ask_query', 'file_qa', 'doc_assist'],
  )
  assert.equal(normalized.find((item) => item.quota_type === 'file_qa')?.quota_name, '文件问答')
  assert.equal(normalized.find((item) => item.quota_type === 'doc_assist')?.current, 1)
})

test('normalizeMyQuotaData returns ordered canonical object view only', () => {
  const normalized = normalizeMyQuotaData({
    quotas: [
      { quota_type: 'reference_preview', quota_name: '旧参考预览', current: 1, limit: 5, remaining: 4, period: 'daily', reset_hint: 'next_day_start' },
      { quota_type: 'file_view', quota_name: '查看原文', current: 2, limit: 10, remaining: 8, period: 'weekly', reset_hint: 'next_week_start' },
      { quota_type: 'pdf_qa', quota_name: '旧 PDF 问答', current: 3, limit: 7, remaining: 4, period: 'monthly', reset_hint: 'next_month_start' },
      { quota_type: 'garbage', quota_name: '垃圾', current: 9, limit: 9, remaining: 0, period: 'daily', reset_hint: 'next_day_start' },
    ],
  })

  assert.deepEqual(Object.keys(normalized), ['file_qa', 'file_view', 'doc_assist'])
  assert.equal(normalized.file_qa.name, '文件问答')
  assert.equal(normalized.doc_assist.windows[0].reset_time, '今日24:00')
})

test('canonical quota type list stays fixed at four buckets', () => {
  assert.deepEqual(CANONICAL_QUOTA_TYPES, ['ask_query', 'file_qa', 'file_view', 'doc_assist'])
})
