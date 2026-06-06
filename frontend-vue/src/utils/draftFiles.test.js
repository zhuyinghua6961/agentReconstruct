import test from 'node:test'
import assert from 'node:assert/strict'

import {
  buildPendingDraftFile,
  buildPendingDraftFileItem,
  defaultPromptForDraftFileType,
  resolveDraftFileType,
} from './draftFiles.js'

test('resolveDraftFileType accepts supported PDF and spreadsheet formats', () => {
  assert.equal(resolveDraftFileType('paper.PDF'), 'pdf')
  assert.equal(resolveDraftFileType('table.xlsx'), 'excel')
  assert.equal(resolveDraftFileType('legacy.xls'), 'excel')
  assert.equal(resolveDraftFileType('data.csv'), 'excel')
  assert.equal(resolveDraftFileType('notes.txt'), '')
})

test('buildPendingDraftFile creates stable draft metadata without uploading', () => {
  const draft = buildPendingDraftFile(
    { name: 'analysis.csv', size: 1024 },
    {
      now: () => '2026-06-06T08:00:00.000Z',
      random: () => 'abc123',
    },
  )

  assert.deepEqual(
    {
      draftId: draft.draftId,
      type: draft.type,
      name: draft.name,
      size: draft.size,
      createdAt: draft.createdAt,
      uploadStatus: draft.uploadStatus,
    },
    {
      draftId: 'draft_20260606080000000_abc123',
      type: 'excel',
      name: 'analysis.csv',
      size: 1024,
      createdAt: '2026-06-06T08:00:00.000Z',
      uploadStatus: 'pending',
    },
  )
})

test('buildPendingDraftFileItem renders draft files as pending file-list rows', () => {
  const draft = buildPendingDraftFile(
    { name: 'battery.pdf', size: 2048 },
    {
      now: () => '2026-06-06T08:00:00.000Z',
      random: () => 'pdf1',
    },
  )

  assert.deepEqual(buildPendingDraftFileItem(draft, 1), {
    type: 'draft-pdf',
    draftId: 'draft_20260606080000000_pdf1',
    title: 'battery.pdf',
    size: 2048,
    displayLabel: '待传2',
    statusLabel: '待发送',
  })
})

test('defaultPromptForDraftFileType keeps file QA prompts centralized', () => {
  assert.equal(defaultPromptForDraftFileType('pdf'), '请帮我总结一下这篇文献的主要内容')
  assert.equal(defaultPromptForDraftFileType('excel'), '请帮我分析一下这个表格的数据')
  assert.equal(defaultPromptForDraftFileType(''), '')
})
