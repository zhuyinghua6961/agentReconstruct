import test from 'node:test'
import assert from 'node:assert/strict'

import {
  CANONICAL_QUOTA_TYPES,
  normalizeQuotaConfigList,
  normalizeUserQuotaList,
} from './quota-normalization.js'

test('normalizeQuotaConfigList collapses legacy patent-adjacent aliases into canonical buckets', () => {
  const normalized = normalizeQuotaConfigList([
    { quota_type: 'kb_qa', quota_name: '知识库问答' },
    { quota_type: 'pdf_qa', quota_name: 'PDF QA' },
    { quota_type: 'tabular_qa', quota_name: 'Table QA' },
    { quota_type: 'hybrid_qa', quota_name: 'Hybrid QA' },
    { quota_type: 'pdf_summary', quota_name: 'PDF Summary' },
    { quota_type: 'text_translate', quota_name: 'Text Translate' },
    { quota_type: 'extract_pdf_text', quota_name: 'Extract PDF Text' },
    { quota_type: 'literature_content', quota_name: 'Literature Content' },
    { quota_type: 'reference_preview', quota_name: 'Reference Preview' },
    { quota_type: 'file_view', quota_name: '查看原文' },
  ])

  assert.deepEqual(
    normalized.map((item) => item.quota_type),
    ['ask_query', 'file_qa', 'file_view', 'doc_assist'],
  )
  assert.equal(normalized.find((item) => item.quota_type === 'ask_query')?.quota_name, '普通问答')
  assert.equal(normalized.find((item) => item.quota_type === 'file_qa')?.quota_name, '文件问答')
  assert.equal(normalized.find((item) => item.quota_type === 'doc_assist')?.quota_name, '文档辅助')
})

test('normalizeUserQuotaList keeps only canonical quota buckets and canonical order', () => {
  const normalized = normalizeUserQuotaList([
    { quota_type: 'hybrid_qa', quota_name: 'Hybrid QA', current: 1, limit: 5, remaining: 4 },
    { quota_type: 'kb_qa', quota_name: '知识库问答', current: 2, limit: 10, remaining: 8 },
    { quota_type: 'reference_preview', quota_name: 'Reference Preview', current: 3, limit: 6, remaining: 3 },
    { quota_type: 'pdf_qa', quota_name: 'PDF QA', current: 4, limit: 5, remaining: 1 },
    { quota_type: 'patent_qa', quota_name: 'Patent QA', current: 9, limit: 9, remaining: 0 },
    { quota_type: 'patent_file_qa', quota_name: 'Patent File QA', current: 9, limit: 9, remaining: 0 },
  ])

  assert.deepEqual(normalized.map((item) => item.quota_type), ['ask_query', 'file_qa', 'doc_assist'])
  assert.ok(normalized.every((item) => CANONICAL_QUOTA_TYPES.includes(item.quota_type)))
  assert.equal(normalized.some((item) => item.quota_type === 'patent_qa'), false)
  assert.equal(normalized.some((item) => item.quota_type === 'patent_file_qa'), false)
})
