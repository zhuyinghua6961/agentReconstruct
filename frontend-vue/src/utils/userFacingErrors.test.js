import test from 'node:test'
import assert from 'node:assert/strict'

import {
  formatHttpError,
  formatUserFacingError,
  humanizeTechnicalMessage,
} from './userFacingErrors.js'

test('formatUserFacingError maps upstream pool timeout code to Chinese', () => {
  assert.equal(
    formatUserFacingError({
      code: 'UPSTREAM_POOL_TIMEOUT',
      error: 'upstream_pool_timeout',
      message: 'upstream_pool_timeout',
    }),
    '模型连接繁忙，请稍后重试'
  )
})

test('humanizeTechnicalMessage preserves existing Chinese text', () => {
  assert.equal(
    humanizeTechnicalMessage('语义检索依赖的 embedding 服务不可用'),
    '语义检索依赖的 embedding 服务不可用'
  )
})

test('formatUserFacingError maps file-not-ready English message', () => {
  assert.equal(
    formatUserFacingError({
      error: 'execution_file_unavailable',
      message: 'uploaded file is not ready for direct reading yet',
    }),
    '文件处理中，请等待就绪后重试'
  )
})

test('formatHttpError returns Chinese status summary', () => {
  assert.equal(formatHttpError(404, '/api/literature_search'), '请求的资源不存在')
  assert.equal(formatHttpError(503, '/api/patent_search'), '服务暂时不可用，请稍后重试')
})
