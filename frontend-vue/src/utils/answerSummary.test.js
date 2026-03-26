import test from 'node:test'
import assert from 'node:assert/strict'

import { formatAnswer, formatStreamingAnswer } from './index.js'

test('summary block stays rendered in both streaming and final markdown paths', () => {
  const markdown = [
    '## 主体答案',
    '',
    '厚电极在高倍率下会出现更强的液相浓差极化。',
    '',
    '## 总结',
    '',
    '- 传质路径更长。',
    '- 盐浓度梯度更陡。',
  ].join('\n')

  const streamingHtml = formatStreamingAnswer(markdown)
  const finalHtml = formatAnswer(markdown)

  assert.match(streamingHtml, /<h2>总结<\/h2>/)
  assert.match(finalHtml, /<h2>总结<\/h2>/)
  assert.match(finalHtml, /<li>传质路径更长。<\/li>/)
  assert.match(finalHtml, /<li>盐浓度梯度更陡。<\/li>/)
})
