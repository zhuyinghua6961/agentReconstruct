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

test('inline ordered list items split correctly after patent citations', () => {
  const markdown = [
    '1. 机械加压与烧结工艺：通过辊压与烧结提升压实密度 (CN108011104A) 2. 掺杂与包覆改性：通过掺杂提升结构稳定性 (CN11442117B) 3. 多元材料体系：优化多元材料设计 (CN106410140A)',
  ].join('\n')

  const streamingHtml = formatStreamingAnswer(markdown)
  const finalHtml = formatAnswer(markdown)

  for (const html of [streamingHtml, finalHtml]) {
    assert.match(html, /<ol>/)
    assert.equal((html.match(/<li>/g) || []).length, 3)
    assert.match(html, /data-patent-id="CN108011104A"/)
    assert.match(html, /data-patent-id="CN11442117B"/)
    assert.match(html, /data-patent-id="CN106410140A"/)
    assert.doesNotMatch(html, /<\/a>\)\s+2\./)
    assert.doesNotMatch(html, /<\/a>\)\s+3\./)
  }
})

test('chapter-scoped numbered subsection items become peer subheadings when glued to patent citations without spaces', () => {
  const markdown = [
    '### 二、工艺参数对压实密度的提升作用',
    '',
    '1. 机械加压与烧结工艺：说明内容 (CN115028154A, CN114873574A)2. 干燥与造粒技术：说明内容 (CN102263247B)3. 烧结制度优化：说明内容',
  ].join('\n')

  const streamingHtml = formatStreamingAnswer(markdown)
  const finalHtml = formatAnswer(markdown)

  for (const html of [streamingHtml, finalHtml]) {
    assert.match(html, /<h3>二、工艺参数对压实密度的提升作用<\/h3>/)
    assert.match(html, /<h3>1\. 机械加压与烧结工艺：<\/h3>/)
    assert.match(html, /<h3>2\. 干燥与造粒技术：<\/h3>/)
    assert.match(html, /<h3>3\. 烧结制度优化：<\/h3>/)
    assert.match(html, />说明内容 \(/)
    assert.match(html, /data-patent-id="CN115028154A"/)
    assert.match(html, /data-patent-id="CN114873574A"/)
    assert.match(html, /data-patent-id="CN102263247B"/)
    assert.doesNotMatch(html, /CN114873574A<\/a>\)2\./)
    assert.doesNotMatch(html, /CN102263247B<\/a>\)3\./)
    assert.doesNotMatch(html, /<ol>/)
  }
})

test('prefixed inline ordered list splits correctly after multi-patent citations without spaces', () => {
  const markdown = '工艺优化包括：1. 机械加压与烧结工艺：说明内容 (CN115028154A, CN114873574A)2. 干燥与造粒技术：说明内容'

  const streamingHtml = formatStreamingAnswer(markdown)
  const finalHtml = formatAnswer(markdown)

  for (const html of [streamingHtml, finalHtml]) {
    assert.match(html, /工艺优化包括：/)
    assert.match(html, /<ol>/)
    assert.equal((html.match(/<li>/g) || []).length, 2)
    assert.match(html, /机械加压与烧结工艺：说明内容/)
    assert.match(html, /干燥与造粒技术：说明内容/)
    assert.match(html, /data-patent-id="CN115028154A"/)
    assert.match(html, /data-patent-id="CN114873574A"/)
    assert.doesNotMatch(html, /CN114873574A<\/a>\)2\./)
  }
})

test('prefixed inline ordered list splits correctly after multi-patent citations joined by ideographic comma', () => {
  const markdown = '工艺优化包括：1. 机械加压与烧结工艺：说明内容 (CN115028154A、CN114873574A)2. 干燥与造粒技术：说明内容 (CN102263247B)3. 烧结制度优化：说明内容'

  const streamingHtml = formatStreamingAnswer(markdown)
  const finalHtml = formatAnswer(markdown)

  for (const html of [streamingHtml, finalHtml]) {
    assert.match(html, /工艺优化包括：/)
    assert.match(html, /<ol>/)
    assert.equal((html.match(/<li>/g) || []).length, 3)
    assert.match(html, /机械加压与烧结工艺：说明内容/)
    assert.match(html, /干燥与造粒技术：说明内容/)
    assert.match(html, /烧结制度优化：说明内容/)
    assert.match(html, /data-patent-id="CN115028154A"/)
    assert.match(html, /data-patent-id="CN114873574A"/)
    assert.match(html, /data-patent-id="CN102263247B"/)
    assert.doesNotMatch(html, /CN114873574A<\/a>\)2\./)
    assert.doesNotMatch(html, /CN102263247B<\/a>\)3\./)
  }
})

test('inline markdown heading with trailing punctuation after patent citation is normalized into a real heading', () => {
  const markdown = [
    '1. 机械加压与烧结工艺：通过辊压与烧结提升压实密度 (CN108011104A) ### 二、工艺参数对压实密度的提升作用。',
    '1. 参数控制：说明',
  ].join('\n')

  const streamingHtml = formatStreamingAnswer(markdown)
  const finalHtml = formatAnswer(markdown)

  for (const html of [streamingHtml, finalHtml]) {
    assert.doesNotMatch(html, /### 二、工艺参数对压实密度的提升作用。/)
    assert.match(html, /<h3>二、工艺参数对压实密度的提升作用。<\/h3>/)
  }
})

test('plain chinese chapter headings and numbered subsection titles are normalized into hierarchical headings', () => {
  const markdown = [
    '一、材料结构与组分设计对压实密度的影响',
    '1. 颗粒级配优化：采用大颗粒与小颗粒混合填充可显著提升压实密度。例如：',
    'CN109192948B 通过将球形小颗粒填充至大颗粒空隙中，使压实密度达 2.69–2.72 g/cm3。',
    'CN107256968A 使用大、小颗粒混杂的磷酸铁原料，使极片压实密度达 2.35–2.45 g/cm3。',
    'CN108011104A 通过大小颗粒浆料混合，压实密度达 2.46–2.57 g/cm3。(CN108011104A) 2. 掺杂与包覆改性：',
    'CN102082266B 采用碳+铁复合包覆，提升导电性和颗粒紧密度。',
    'CN101442117B 通过碳包覆与喷雾干燥工艺，改善颗粒分布。(CN101442117B) 3. 多元材料体系：',
    'CN116986574A 针对磷酸锰铁锂（LMFP），通过多粒径混合使压实密度达 2.8–3.0 g/cm3。',
  ].join('\n')

  const streamingHtml = formatStreamingAnswer(markdown)
  const finalHtml = formatAnswer(markdown)

  for (const html of [streamingHtml, finalHtml]) {
    assert.match(html, /<h2>一、材料结构与组分设计对压实密度的影响<\/h2>/)
    assert.match(html, /<h3>1\. 颗粒级配优化：<\/h3>/)
    assert.match(html, /<h3>2\. 掺杂与包覆改性：<\/h3>/)
    assert.match(html, /<h3>3\. 多元材料体系：<\/h3>/)
    assert.match(html, /采用大颗粒与小颗粒混合填充可显著提升压实密度。例如：/)
    assert.match(html, /data-patent-id="CN108011104A"/)
    assert.match(html, /data-patent-id="CN101442117B"/)
    assert.doesNotMatch(html, /CN108011104A<\/a>\)\s*2\./)
    assert.doesNotMatch(html, /CN101442117B<\/a>\)\s*3\./)
    assert.doesNotMatch(html, /<ol start="2">/)
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

test('literature summary keeps limitations chapter and consistent structure in streaming and final render paths', () => {
  const markdown = [
    '## 研究目的和背景',
    '- 该研究围绕复杂场景中的空中视觉语言导航任务展开。',
    '',
    '## 研究方法/实验设计',
    '- 方法链路包括：',
    '  - 视觉感知与目标定位。',
    '  - 局部地图构建与表示编码。',
    '  - 语言模型推理与动作决策。',
    '',
    '## 主要发现和结果',
    '- 该方法在多个评估指标上优于对比基线。',
    '',
    '## 结论和意义',
    '- 结果说明多模态空间表示能够提升导航决策质量。',
    '',
    '## 局限性',
    '- 原文指出复杂天气与长距离场景下仍存在性能下降。',
    '',
    '注*：所有总结内容均严格基于文件原文中明确提到的信息，未添加任何通用知识或推测内容。',
  ].join('\n')

  const streamingHtml = formatStreamingAnswer(markdown)
  const finalHtml = formatAnswer(markdown)

  const expectedHeadings = [
    '研究目的和背景',
    '研究方法/实验设计',
    '主要发现和结果',
    '结论和意义',
    '局限性',
  ]

  for (const html of [streamingHtml, finalHtml]) {
    for (const heading of expectedHeadings) {
      assert.match(html, new RegExp(`<h2>${heading}<\\/h2>`))
    }
    assert.match(html, /<li>(?:<p>)?方法链路包括：(?:<\/p>)?\s*<ul>/)
    assert.match(html, /视觉感知与目标定位。/)
    assert.match(html, /局部地图构建与表示编码。/)
    assert.match(html, /语言模型推理与动作决策。/)
    assert.match(html, /<p class="message-note">注\*：所有总结内容均严格基于文件原文中明确提到的信息，未添加任何通用知识或推测内容。<\/p>/)
  }

  assert.deepEqual(
    streamingHtml.match(/<h2>[^<]+<\/h2>/g),
    finalHtml.match(/<h2>[^<]+<\/h2>/g)
  )
  assert.equal(
    (streamingHtml.match(/<li>/g) || []).length,
    (finalHtml.match(/<li>/g) || []).length
  )
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
