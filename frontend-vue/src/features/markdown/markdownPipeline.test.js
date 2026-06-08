import test from 'node:test'
import assert from 'node:assert/strict'

import {
  parseMarkdownContent,
  renderMarkdownContentToHtml,
} from './markdownPipeline.js'

function collectInlineTokens(tokens, type) {
  const found = []
  const visitToken = (token) => {
    if (!token || typeof token !== 'object') return
    if (token.type === type) found.push(token)
    for (const key of ['tokens', 'items', 'header', 'rows']) {
      const value = token[key]
      if (!Array.isArray(value)) continue
      for (const child of value.flat(Infinity)) {
        visitToken(child)
      }
    }
  }
  for (const token of tokens || []) {
    visitToken(token)
  }
  return found
}

test('parseMarkdownContent renders raw HTML as escaped text for final and streaming output', () => {
  const input = '<img src=x onerror=alert(1)>'
  const finalModel = parseMarkdownContent(input)
  const streamingModel = parseMarkdownContent(input, { streaming: true })
  const finalHtml = renderMarkdownContentToHtml(input)
  const streamingHtml = renderMarkdownContentToHtml(input, { streaming: true })

  assert.equal(finalModel.diagnostics.rawHtmlTokenCount, 1)
  assert.equal(streamingModel.diagnostics.rawHtmlTokenCount, 1)
  assert.match(finalHtml, /&lt;img src=x onerror=alert\(1\)&gt;/)
  assert.match(streamingHtml, /&lt;img src=x onerror=alert\(1\)&gt;/)
  assert.doesNotMatch(finalHtml, /<img/i)
  assert.doesNotMatch(streamingHtml, /<img/i)
})

test('parseMarkdownContent leaves ordinary pipe prose alone instead of inserting fake tables', () => {
  const input = 'A | B\nC | D'
  const model = parseMarkdownContent(input)
  const html = renderMarkdownContentToHtml(input)

  assert.doesNotMatch(model.normalized, /\|\|/)
  assert.equal(collectInlineTokens(model.tokens, 'tableCellLink').length, 0)
  assert.doesNotMatch(html, /<table/i)
  assert.match(html, /A \| B/)
  assert.match(html, /C \| D/)
})

test('parseMarkdownContent keeps approximate numeric tildes as text while preserving double-tilde deletion', () => {
  const input = '结构稳定（~-250 J g^-1）远低于层状（<~-941 J g^-1），但 ~~失效~~。'
  const model = parseMarkdownContent(input)
  const html = renderMarkdownContentToHtml(input)
  const delTokens = collectInlineTokens(model.tokens, 'del')

  assert.equal(delTokens.length, 1)
  assert.match(html, /结构稳定（~-250 J g\^-1）远低于层状（&lt;~-941 J g\^-1），但 <del>失效<\/del>。/)
  assert.doesNotMatch(html, /<del>-250/)
})

test('parseMarkdownContent linkifies DOI and patent text but skips code spans and fenced code', () => {
  const input = [
    '正文 10.1000/demo 与 CN109192948B。',
    '',
    '`10.1000/code` `CN115692635A`',
    '',
    '```',
    '10.1000/fence CN100420075C',
    '```',
  ].join('\n')
  const model = parseMarkdownContent(input)
  const html = renderMarkdownContentToHtml(input)

  assert.equal(model.diagnostics.doiLinkCount, 1)
  assert.equal(model.diagnostics.patentLinkCount, 1)
  assert.match(html, /data-doi="10\.1000\/demo"/)
  assert.match(html, /data-patent-id="CN109192948B"/)
  assert.match(html, /<code>10\.1000\/code<\/code>/)
  assert.match(html, /<code>CN115692635A<\/code>/)
  assert.match(html, /<pre[\s\S]*10\.1000\/fence CN100420075C[\s\S]*<\/pre>/)
  assert.equal((html.match(/class="doi-link"/g) || []).length, 1)
  assert.equal((html.match(/class="doi-link patent-link"/g) || []).length, 1)
})

test('parseMarkdownContent emits math tokens and does not treat DOI underscores as math', () => {
  const input = '容量 $Q_{loss} = kx^2$，参考 DOI 10.1155/2014_364327。'
  const model = parseMarkdownContent(input)
  const html = renderMarkdownContentToHtml(input)
  const mathTokens = collectInlineTokens(model.tokens, 'inlineMath')

  assert.equal(model.diagnostics.mathTokenCount, 1)
  assert.equal(mathTokens.length, 1)
  assert.equal(mathTokens[0].text, 'Q_{loss} = kx^2')
  assert.match(html, /class="katex"/)
  assert.match(html, /data-doi="10\.1155\/2014_364327"/)
  assert.doesNotMatch(html, /364327<\/sub>/)
})

test('parseMarkdownContent renders bracket-delimited math inside prose', () => {
  const input = '由 \\[D = D_0 \\exp(-E_a/kT)\\] 可知温度升高会加快扩散。'
  const model = parseMarkdownContent(input)
  const html = renderMarkdownContentToHtml(input)
  const mathTokens = collectInlineTokens(model.tokens, 'inlineMath')

  assert.equal(model.diagnostics.mathTokenCount, 1)
  assert.equal(mathTokens.length, 1)
  assert.equal(mathTokens[0].text, 'D = D_0 \\exp(-E_a/kT)')
  assert.match(html, /class="katex"/)
  assert.doesNotMatch(html, /\\\[/)
  assert.doesNotMatch(html, /\\\]/)
})

test('parseMarkdownContent renders parenthesis-delimited math after marked escape tokenization', () => {
  const input = '变量 \\(x\\) 与扩散长度 \\(L^2 = 2Dt\\) 相关。'
  const model = parseMarkdownContent(input)
  const html = renderMarkdownContentToHtml(input)
  const mathTokens = collectInlineTokens(model.tokens, 'inlineMath')

  assert.equal(model.diagnostics.mathTokenCount, 2)
  assert.equal(mathTokens.length, 2)
  assert.deepEqual(
    mathTokens.map((token) => token.text),
    ['x', 'L^2 = 2Dt'],
  )
  assert.match(html, /class="katex"/)
  assert.doesNotMatch(html, /\\\(/)
  assert.doesNotMatch(html, /\\\)/)
})

test('parseMarkdownContent renders model-style parenthesized LaTeX math', () => {
  const input = [
    '扩散系数 ((D_{\\text{Li}})) 存在差异。',
    '关系（L^2 \\propto \\kappa t），驱动力(\\Delta \\mu_{\\text{Li}})相关。',
    '速度(\\nu_{\\text{Li}} = \\text{d}L/\\text{d}t)增加。',
  ].join('\n')
  const model = parseMarkdownContent(input)
  const html = renderMarkdownContentToHtml(input)
  const mathTokens = collectInlineTokens(model.tokens, 'inlineMath')

  assert.equal(mathTokens.length, 4)
  assert.deepEqual(
    mathTokens.map((token) => token.text),
    [
      'D_{\\text{Li}}',
      'L^2 \\propto \\kappa t',
      '\\Delta \\mu_{\\text{Li}}',
      '\\nu_{\\text{Li}} = \\text{d}L/\\text{d}t',
    ],
  )
  assert.equal(model.diagnostics.mathTokenCount, 4)
  assert.equal((html.match(/class="katex"/g) || []).length, 4)
  assert.match(html, /<msub>/)
  assert.match(html, /∝/)
  assert.match(html, /Δ/)
  assert.doesNotMatch(html, /\(\(/)
  assert.doesNotMatch(html, /\)\)/)
})

test('parseMarkdownContent renders bare compact LaTeX formulas without touching prose identifiers', () => {
  const input = 'LiFePO_{4}/FePO_{4} 相界面中，电导率约为 10^{-4} \\text{ S/cm}，普通变量 file_name 保持文本，DOI 10.1155/2014_364327 可点击。'
  const model = parseMarkdownContent(input)
  const html = renderMarkdownContentToHtml(input)
  const mathTokens = collectInlineTokens(model.tokens, 'inlineMath')

  assert.equal(model.diagnostics.doiLinkCount, 1)
  assert.equal(mathTokens.length, 3)
  assert.deepEqual(
    mathTokens.map((token) => token.text),
    ['LiFePO_{4}/FePO_{4}', '10^{-4}', '\\text{ S/cm}'],
  )
  assert.match(html, /class="katex"/)
  assert.match(html, /file_name/)
  assert.match(html, /data-doi="10\.1155\/2014_364327"/)
  assert.doesNotMatch(html, /2014<\/sub>/)
})

test('parseMarkdownContent renders unbraced TeX scripts without touching prose identifiers', () => {
  const input = '扩散项 x_i 与 D_0、\\exp(-E_a/kT) 有关，普通变量 file_name 保持文本。'
  const model = parseMarkdownContent(input)
  const html = renderMarkdownContentToHtml(input)
  const mathTokens = collectInlineTokens(model.tokens, 'inlineMath')

  assert.deepEqual(
    mathTokens.map((token) => token.text),
    ['x_i', 'D_0', '\\exp(-E_a/kT)'],
  )
  assert.equal(model.diagnostics.mathTokenCount, 3)
  assert.match(html, /class="katex"/)
  assert.match(html, /file_name/)
  assert.doesNotMatch(html, /file<sub>name<\/sub>/)
})

test('parseMarkdownContent avoids implicit math in ordinary parentheses and code spans', () => {
  const input = '循环伏安（CV）和粒径（203 nm）不是公式，`D_{\\text{Li}}` 也保持代码。'
  const model = parseMarkdownContent(input)
  const html = renderMarkdownContentToHtml(input)

  assert.equal(model.diagnostics.mathTokenCount, 0)
  assert.equal(collectInlineTokens(model.tokens, 'inlineMath').length, 0)
  assert.match(html, /循环伏安（CV）/)
  assert.match(html, /粒径（203 nm）/)
  assert.match(html, /<code>D_\{\\text\{Li\}\}<\/code>/)
  assert.doesNotMatch(html, /class="katex"/)
})

test('parseMarkdownContent linkifies patent IDs after patent ID labels', () => {
  const input = '专利证据显示其热力学稳定性较强（专利 ID=CN114906831B）。'
  const model = parseMarkdownContent(input)
  const html = renderMarkdownContentToHtml(input)
  const patentTokens = collectInlineTokens(model.tokens, 'patentLink')

  assert.equal(model.diagnostics.patentLinkCount, 1)
  assert.equal(patentTokens.length, 1)
  assert.equal(patentTokens[0].patentId, 'CN114906831B')
  assert.match(html, /专利 ID=<a href="#" class="doi-link patent-link" data-patent-id="CN114906831B">CN114906831B<\/a>/)
})

test('parseMarkdownContent renders inline display-math delimiters without stray dollar signs', () => {
  const input = '明确的分解路径：$$2 \\mathrm{FePO}{4} \\rightarrow \\mathrm{Fe}{2}\\mathrm{P}{2}\\mathrm{O}_{7}$$ 该反应发生。'
  const model = parseMarkdownContent(input)
  const html = renderMarkdownContentToHtml(input)
  const mathTokens = collectInlineTokens(model.tokens, 'inlineMath')

  assert.equal(model.diagnostics.mathTokenCount, 1)
  assert.equal(mathTokens.length, 1)
  assert.equal(mathTokens[0].text, '2 \\mathrm{FePO}{4} \\rightarrow \\mathrm{Fe}{2}\\mathrm{P}{2}\\mathrm{O}_{7}')
  assert.match(html, /class="katex"/)
  assert.doesNotMatch(html, /\$\$/)
  assert.doesNotMatch(html, /O_\{7\}\$/)
})

test('parseMarkdownContent splits glued heading body after a Chinese colon', () => {
  const input = '### 3. 与层状氧化物的对比：本质差异决定热稳定性鸿沟 层状氧化物中，氧原子仅与过渡金属形成较弱的离子-共价混合键。'
  const model = parseMarkdownContent(input)

  assert.equal(model.tokens[0].type, 'heading')
  assert.equal(model.tokens[0].text, '3. 与层状氧化物的对比：')
  assert.equal(model.tokens[1].type, 'paragraph')
  assert.match(model.tokens[1].text, /^本质差异决定热稳定性鸿沟/)
})

test('parseMarkdownContent renders patent message headings as compact bold paragraphs', () => {
  const input = [
    '### 技术结论',
    'CN109192948B 显示该方案可改善循环稳定性。',
  ].join('\n')
  const defaultModel = parseMarkdownContent(input)
  const patentModel = parseMarkdownContent(input, { variant: 'patent-message' })

  assert.equal(defaultModel.tokens[0].type, 'heading')
  assert.equal(patentModel.tokens[0].type, 'paragraph')
  assert.equal(patentModel.tokens[0].tokens[0].type, 'strong')
  assert.equal(patentModel.tokens[0].tokens[0].text, '技术结论')
  assert.equal(patentModel.diagnostics.patentLinkCount, 1)
})
