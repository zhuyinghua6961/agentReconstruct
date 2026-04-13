import test from 'node:test'
import assert from 'node:assert/strict'

import { formatAnswer, formatStreamingAnswer } from './index.js'

function installMinimalDocumentStub() {
  if (globalThis.document?.createElement) return
  globalThis.document = {
    createElement() {
      return {
        _text: '',
        set textContent(value) {
          this._text = String(value ?? '')
        },
        get innerHTML() {
          return this._text
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;')
        },
      }
    },
  }
}

installMinimalDocumentStub()

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
  assert.match(finalHtml, /<li>(?:<p>)?传质路径更长。(?:<\/p>)?[\s\S]*?<\/li>/)
  assert.match(finalHtml, /<li>(?:<p>)?盐浓度梯度更陡。(?:<\/p>)?[\s\S]*?<\/li>/)
})

test('inline markdown heading marker after sentence is normalized into a real heading', () => {
  const markdown = [
    '围绕磷酸铁锂的标称平台电压，通常可概括如下。### 核心电压参数',
    '- 标称电压通常约为 3.2V。',
  ].join('\n')

  const streamingHtml = formatStreamingAnswer(markdown)
  const finalHtml = formatAnswer(markdown)

  assert.doesNotMatch(streamingHtml, /### 核心电压参数/)
  assert.doesNotMatch(finalHtml, /### 核心电压参数/)
  assert.match(streamingHtml, /<h3>核心电压参数<\/h3>/)
  assert.match(finalHtml, /<h3>核心电压参数<\/h3>/)
})

test('inline list marker after patent citation punctuation is normalized into a real list', () => {
  const markdown = [
    '掺杂效应可参考 (CN102386398A)。 - 包覆层作用能够降低副反应。 - 复合正极材料可提升循环稳定性。',
  ].join('\n')

  const streamingHtml = formatStreamingAnswer(markdown)
  const finalHtml = formatAnswer(markdown)

  for (const html of [streamingHtml, finalHtml]) {
    assert.match(html, /data-patent-id="CN102386398A"/)
    assert.match(html, /<li>(?:<p>)?包覆层作用能够降低副反应。(?:<\/p>)?[\s\S]*?<\/li>/)
    assert.match(html, /<li>(?:<p>)?复合正极材料可提升循环稳定性。(?:<\/p>)?[\s\S]*?<\/li>/)
    assert.doesNotMatch(html, /\)\。\s+- 包覆层作用/)
  }
})

test('inline markdown heading marker after plain text is normalized into a real heading', () => {
  const markdown = [
    '全电池性能 ### 总结',
    '- 循环寿命更稳定。',
  ].join('\n')

  const streamingHtml = formatStreamingAnswer(markdown)
  const finalHtml = formatAnswer(markdown)

  for (const html of [streamingHtml, finalHtml]) {
    assert.doesNotMatch(html, /### 总结/)
    assert.match(html, /<h3>总结<\/h3>/)
    assert.match(html, /<li>(?:<p>)?循环寿命更稳定。(?:<\/p>)?[\s\S]*?<\/li>/)
  }
})

test('mixed separator and heading markers are normalized instead of leaking raw markdown tokens', () => {
  const markdown = '引言 --- #### # 1. 本征电压平台与机制'

  const streamingHtml = formatStreamingAnswer(markdown)
  const finalHtml = formatAnswer(markdown)

  for (const html of [streamingHtml, finalHtml]) {
    assert.doesNotMatch(html, /--- #### #/)
    assert.doesNotMatch(html, /#### # 1\./)
    assert.match(html, /<hr\b/)
    assert.match(html, /<(?:h1|h2|h3|h4|h5|h6)>1\. 本征电压平台与机制<\/(?:h1|h2|h3|h4|h5|h6)>/)
  }
})

test('literal markdown heading examples remain plain text instead of becoming real headings', () => {
  const markdown = 'Markdown 中使用 ### 标记三级标题。'

  const streamingHtml = formatStreamingAnswer(markdown)
  const finalHtml = formatAnswer(markdown)

  for (const html of [streamingHtml, finalHtml]) {
    assert.match(html, /Markdown 中使用/)
    assert.match(html, /### 标记三级标题。/)
    assert.doesNotMatch(html, /<h3>标记三级标题。<\/h3>/)
  }
})

test('literal markdown heading examples stay plain text even when followed by a real list', () => {
  const markdown = [
    'Markdown 中使用 ### 标记三级标题。',
    '- 列表项',
  ].join('\n')

  const streamingHtml = formatStreamingAnswer(markdown)
  const finalHtml = formatAnswer(markdown)

  for (const html of [streamingHtml, finalHtml]) {
    assert.match(html, /Markdown 中使用 ### 标记三级标题。/)
    assert.doesNotMatch(html, /<h3>标记三级标题。<\/h3>/)
    assert.match(html, /<li>(?:<p>)?列表项(?:<\/p>)?[\s\S]*?<\/li>/)
  }
})

test('dash-separated prose inside a single bullet item is not split into multiple bullets', () => {
  const markdown = '- Na盐体系 - 电导率更高，但成本也更高。'

  const streamingHtml = formatStreamingAnswer(markdown)
  const finalHtml = formatAnswer(markdown)

  for (const html of [streamingHtml, finalHtml]) {
    assert.equal((html.match(/<li>/g) || []).length, 1)
    assert.match(html, /Na盐体系/)
    assert.match(html, /电导率更高，但成本也更高。/)
  }
})

test('inline bullet lists still split when later items start with digits', () => {
  const markdown = '参考：- 2024年容量提升 - 2025年循环稳定'

  const streamingHtml = formatStreamingAnswer(markdown)
  const finalHtml = formatAnswer(markdown)

  for (const html of [streamingHtml, finalHtml]) {
    assert.match(html, /<li>(?:<p>)?2024年容量提升(?:<\/p>)?[\s\S]*?<\/li>/)
    assert.match(html, /<li>(?:<p>)?2025年循环稳定(?:<\/p>)?[\s\S]*?<\/li>/)
  }
})

test('inline ordered list items are split into separate ordered list entries', () => {
  const markdown = '1.材料本征特性...。2.改性优化...。3.测试条件...。'

  const streamingHtml = formatStreamingAnswer(markdown)
  const finalHtml = formatAnswer(markdown)

  for (const html of [streamingHtml, finalHtml]) {
    assert.match(html, /<ol>/)
    assert.equal((html.match(/<li>/g) || []).length, 3)
    assert.match(html, /材料本征特性/)
    assert.match(html, /改性优化/)
    assert.match(html, /测试条件/)
  }
})

test('literature summary chapters keep nested bullets and render note as a secondary paragraph', () => {
  const markdown = [
    '## 研究目的和背景',
    '- 该研究关注厚电极在高面容量条件下的传质限制。',
    '',
    '## 研究方法/实验设计',
    '- 研究对象为自支撑 LiMn2O4 厚电极。',
    '- 表征与验证：',
    '  - XRD 跟踪 (111) 峰移动。',
    '  - TOF-SIMS 观察 Li+ 分布。',
    '',
    '## 主要发现和结果',
    '- OCV 平台与浓差极化存在明显差异。',
    '',
    '## 结论和意义',
    '- 结果支持厚电极设计需要同步优化离子输运。',
    '',
    '注*：所有总结内容均严格基于 PDF 原文中明确提到的信息。',
  ].join('\n')

  const streamingHtml = formatStreamingAnswer(markdown)
  const finalHtml = formatAnswer(markdown)

  for (const html of [streamingHtml, finalHtml]) {
    assert.doesNotMatch(html, /## 研究目的和背景/)
    assert.match(html, /<h2>研究目的和背景<\/h2>/)
    assert.match(html, /<h2>研究方法\/实验设计<\/h2>/)
    assert.match(html, /<h2>主要发现和结果<\/h2>/)
    assert.match(html, /<h2>结论和意义<\/h2>/)
    assert.match(html, /<li>(?:<p>)?表征与验证：(?:<\/p>)?\s*<ul>/)
    assert.match(html, /XRD 跟踪 \(111\) 峰移动。/)
    assert.match(html, /TOF-SIMS 观察 Li\+ 分布。/)
    assert.match(html, /<p class="message-note">注\*：所有总结内容均严格基于 PDF 原文中明确提到的信息。<\/p>/)
    assert.doesNotMatch(html, /<h[1-6]>注\*：/)
  }
})

test('graph kb markdown renders section headings, literature entries, and doi links cleanly', () => {
  const markdown = [
    '## 📚 文献概览',
    '- 当前展示 2 篇相关文献',
    '- 原料：LiFePO4',
    '',
    '## 📖 相关文献',
    '### [1] Paper A',
    '- DOI：10.1/a',
    '- 命中条件：原料 = LiFePO4 powder',
  ].join('\n')

  const finalHtml = formatAnswer(markdown)

  assert.match(finalHtml, /<h2>📚 文献概览<\/h2>/)
  assert.match(finalHtml, /<h2>📖 相关文献<\/h2>/)
  assert.match(finalHtml, /<h3>\[1\] Paper A<\/h3>/)
  assert.match(finalHtml, /<li>(?:<p>)?当前展示 2 篇相关文献(?:<\/p>)?[\s\S]*?<\/li>/)
  assert.match(finalHtml, /class="doi-link"/)
  assert.match(finalHtml, /data-doi="10\.1\/a"/)
})
