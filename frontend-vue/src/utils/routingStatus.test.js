import test from 'node:test'
import assert from 'node:assert/strict'

import { buildRoutingErrorMarkdown, buildRoutingErrorPresentation, getRouteModeLabel, mergeRoutingMetadata } from './routingStatus.js'

test('getRouteModeLabel maps known routes to readable labels', () => {
  assert.equal(getRouteModeLabel('kb_qa'), '知识库问答')
  assert.equal(getRouteModeLabel('pdf_qa'), 'PDF问答')
  assert.equal(getRouteModeLabel('tabular_qa'), '表格问答')
  assert.equal(getRouteModeLabel('hybrid_qa'), '混合文件问答')
})

test('mergeRoutingMetadata keeps route context from metadata and error events', () => {
  const metadata = mergeRoutingMetadata(
    { route: 'pdf_qa', source_scope: 'pdf', selected_file_ids: [11] },
    {
      code: 'FILE_NOT_READY',
      message: '文件 processing.pdf 处理中',
      retriable: true,
    }
  )

  assert.equal(metadata.route, 'pdf_qa')
  assert.equal(metadata.source_scope, 'pdf')
  assert.deepEqual(metadata.selected_file_ids, [11])
  assert.equal(metadata.error_code, 'FILE_NOT_READY')
  assert.equal(metadata.error_message, '文件 processing.pdf 处理中')
  assert.equal(metadata.retriable, true)
})

test('mergeRoutingMetadata preserves timing metadata for live stage display', () => {
  const fromStageTimings = mergeRoutingMetadata(
    { route: 'kb_qa' },
    { stage_timings_ms: { stage1: 1000, stage2: 2000 } }
  )
  assert.deepEqual(fromStageTimings.timings, { stage1: 1000, stage2: 2000 })
  assert.deepEqual(fromStageTimings.stage_timings_ms, { stage1: 1000, stage2: 2000 })

  const fromTimings = mergeRoutingMetadata(
    { timings: { stage1: 1000 } },
    { timings: { stage1: 1000, stage2: 2000 } }
  )
  assert.deepEqual(fromTimings.timings, { stage1: 1000, stage2: 2000 })

  const incremental = mergeRoutingMetadata(
    { timings: { stage1: 1000 } },
    { stage_timings_ms: { stage2: 2000 } }
  )
  assert.deepEqual(incremental.timings, { stage1: 1000, stage2: 2000 })
  assert.deepEqual(incremental.stage_timings_ms, { stage2: 2000 })
})

test('buildRoutingErrorMarkdown formats clarification with candidate list', () => {
  const markdown = buildRoutingErrorMarkdown({
    code: 'FILE_SELECTION_CLARIFICATION_REQUIRED',
    message: '当前对话中有多个候选文件，请明确指定文件',
    metadata: {
      route: 'pdf_qa',
      clarify_candidates: [
        { file_id: 11, display_no: 1, file_name: 'paper-a.pdf', file_type: 'pdf', processing_stage: 'ready' },
        { file_id: 22, display_no: 2, file_name: 'paper-b.pdf', file_type: 'pdf', processing_stage: 'indexing' },
      ],
    },
  })

  assert.match(markdown, /需要你进一步明确本轮要使用的文件/)
  assert.match(markdown, /当前意图路由：PDF问答/)
  assert.match(markdown, /#1 paper-a\.pdf（PDF，就绪）/)
  assert.match(markdown, /#2 paper-b\.pdf（PDF，索引中）/)
})

test('buildRoutingErrorMarkdown formats file-not-ready status with retry guidance', () => {
  const markdown = buildRoutingErrorMarkdown({
    code: 'FILE_NOT_READY',
    message: '文件 processing.pdf 正在索引中，请稍后重试',
    metadata: {
      route: 'pdf_qa',
      selected_file_ids: [44],
    },
  })

  assert.match(markdown, /目标文件还在处理中/)
  assert.match(markdown, /当前路由：PDF问答/)
  assert.match(markdown, /已选文件：#44/)
  assert.match(markdown, /等待文件状态变为“就绪”后再重试/)
})

test('buildRoutingErrorPresentation returns quota card model for chat quota failures', () => {
  const presentation = buildRoutingErrorPresentation({
    code: 'QUOTA_EXCEEDED',
    message: 'quota exceeded',
    metadata: {
      route: 'pdf_qa',
    },
    data: {
      quota_type: 'file_qa',
      quota_name: '文件问答',
      current: 20,
      limit: 20,
      remaining: 0,
      reset_hint: 'next_day_start',
    },
  })

  assert.equal(presentation.kind, 'quota_card')
  assert.equal(presentation.card.variant, 'quota_exceeded')
  assert.equal(presentation.card.featureTitle, '文件问答')
  assert.equal(presentation.card.quotaType, 'file_qa')
})
