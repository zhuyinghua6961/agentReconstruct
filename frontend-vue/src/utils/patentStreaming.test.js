import test from 'node:test'
import assert from 'node:assert/strict'

import { buildMessageRenderMemoKey } from './messageRenderMemo.js'
import {
  buildPatentStreamingCapability,
  getPatentPreviewStreams,
  isStructuredPatentContentEvent,
  reducePatentStreamingState,
} from './patentStreaming.js'

test('buildPatentStreamingCapability only enables patent requests with file-context hints', () => {
  assert.deepEqual(
    buildPatentStreamingCapability({
      mode: 'thinking',
      pdfContext: { all_available_ids: [11] },
    }),
    {
      enabled: false,
      headers: {},
      requestOptions: {},
    },
  )

  assert.equal(
    buildPatentStreamingCapability({
      mode: 'patent',
      pdfContext: { all_available_ids: [11] },
    }).headers['X-Patent-Stream-Capability'],
    'preview_v1',
  )
})

test('reducePatentStreamingState buckets preview content by content_stream_id', () => {
  let state = undefined

  ;[
    {
      type: 'content',
      content_role: 'preview',
      content_source: 'pdf',
      content_stream_id: 'pdf:primary',
      content_phase: 'start',
      replace_stream: true,
      content: '第一段',
    },
    {
      type: 'content',
      content_role: 'preview',
      content_source: 'table',
      content_stream_id: 'table:primary',
      content_phase: 'snapshot',
      replace_stream: true,
      content: '表格结论',
    },
    {
      type: 'content',
      content_role: 'preview',
      content_source: 'pdf',
      content_stream_id: 'pdf:primary',
      content_phase: 'delta',
      content: '第二段',
    },
  ].forEach((event) => {
    state = reducePatentStreamingState(state, event).state
  })

  const streams = getPatentPreviewStreams(state)
  assert.equal(streams.length, 2)
  assert.equal(streams[0].contentStreamId, 'pdf:primary')
  assert.equal(streams[0].content, '第一段第二段')
  assert.equal(streams[1].contentStreamId, 'table:primary')
  assert.equal(streams[1].content, '表格结论')
  assert.equal(streams[1].completed, true)
})

test('replace_stream only clears the targeted preview bucket', () => {
  let state = undefined

  ;[
    {
      type: 'content',
      content_role: 'preview',
      content_source: 'pdf',
      content_stream_id: 'pdf:primary',
      content_phase: 'snapshot',
      replace_stream: true,
      content: '旧 PDF',
    },
    {
      type: 'content',
      content_role: 'preview',
      content_source: 'table',
      content_stream_id: 'table:primary',
      content_phase: 'snapshot',
      replace_stream: true,
      content: '旧表格',
    },
    {
      type: 'content',
      content_role: 'preview',
      content_source: 'pdf',
      content_stream_id: 'pdf:primary',
      content_phase: 'snapshot',
      replace_stream: true,
      content: '新 PDF',
    },
  ].forEach((event) => {
    state = reducePatentStreamingState(state, event).state
  })

  const streams = getPatentPreviewStreams(state)
  assert.equal(streams.find((item) => item.contentStreamId === 'pdf:primary')?.content, '新 PDF')
  assert.equal(streams.find((item) => item.contentStreamId === 'table:primary')?.content, '旧表格')
})

test('structured final events are recognized and kept out of preview buckets', () => {
  const event = {
    type: 'content',
    content_role: 'final',
    content_source: 'hybrid',
    content_phase: 'start',
    replace_stream: true,
    content: '最终答案',
  }

  assert.equal(isStructuredPatentContentEvent(event), true)

  const reduced = reducePatentStreamingState(undefined, event)
  assert.equal(reduced.handled, true)
  assert.equal(reduced.mainContentMode, 'final')
  assert.equal(reduced.content, '最终答案')
  assert.equal(getPatentPreviewStreams(reduced.state).length, 0)
})

test('message render memo key changes when patent preview buckets change', () => {
  const message = {
    role: 'assistant',
    content: '',
    metadata: {},
  }

  const initialKey = buildMessageRenderMemoKey(message)
  const previewState = reducePatentStreamingState(message, {
    type: 'content',
    content_role: 'preview',
    content_source: 'pdf',
    content_stream_id: 'pdf:primary',
    content_phase: 'snapshot',
    replace_stream: true,
    content: '预览内容',
  }).state

  message.patentStreaming = previewState
  message.metadata = {
    patent_streaming: previewState,
  }

  const previewKey = buildMessageRenderMemoKey(message)
  assert.notEqual(previewKey, initialKey)
})
