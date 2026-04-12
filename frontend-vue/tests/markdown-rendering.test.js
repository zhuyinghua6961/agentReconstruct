import test from 'node:test'
import assert from 'node:assert/strict'

import { formatAnswer, formatStreamingAnswer } from '../src/utils/index.js'

function installMinimalDom() {
  global.document = {
    createElement() {
      let text = ''
      return {
        set textContent(value) {
          text = String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;')
        },
        get innerHTML() {
          return text
        },
      }
    },
  }
}

test('formatStreamingAnswer renders markdown tables during streaming', () => {
  installMinimalDom()
  const input = [
    '| 材料 | 厚度 |',
    '| --- | --- |',
    '| 电极A | 120 um |',
    '| 电极B | 180 um |',
    '',
    '[DOI: 10.1000/demo]',
  ].join('\n')

  const html = formatStreamingAnswer(input)

  assert.match(html, /<table>/i)
  assert.match(html, /<td>120 um<\/td>/i)
  assert.match(html, /class="doi-link"/i)
  assert.match(html, /data-doi="10\.1000\/demo"/i)
})

test('formatAnswer repairs polluted DOI links and splits concatenated DOI tokens', () => {
  installMinimalDom()
  const input = [
    '[DOI: 10.1016j.est.2024.113859]',
    '[DOI: 10.1016j.jpowsour.2005.03.09910.1016j.jpowsour.2013.06.070]',
  ].join(' ')

  const html = formatAnswer(input)

  assert.match(html, /data-doi="10\.1016\/j\.est\.2024\.113859"/i)
  assert.match(html, /data-doi="10\.1016\/j\.jpowsour\.2005\.03\.099"/i)
  assert.match(html, /data-doi="10\.1016\/j\.jpowsour\.2013\.06\.070"/i)
  assert.doesNotMatch(html, /data-doi="10\.1016j/i)
})

test('formatAnswer renders DOI links with parenthesized suffix intact', () => {
  installMinimalDom()
  const input = '参考文献 (doi=10.1016/S0378-7753(03)00297-0)'

  const html = formatAnswer(input)

  assert.match(html, /data-doi="10\.1016\/S0378-7753\(03\)00297-0"/i)
  assert.match(html, />10\.1016\/S0378-7753\(03\)00297-0<\/a>/i)
})

test('formatAnswer renders inline formulas with superscript and subscript', () => {
  installMinimalDom()
  const input = '容量衰减可写作 $Q_{loss} = k x^2$，材料可表示为 Li_{1-x}CoO_2。'

  const html = formatAnswer(input)

  assert.doesNotMatch(html, /\$Q_\{loss\} = k x\^2\$/)
  assert.match(html, /<sub>loss<\/sub>/i)
  assert.match(html, /<sup>2<\/sup>/i)
  assert.match(html, /Li<sub>1-x<\/sub>CoO<sub>2<\/sub>/i)
})

test('formatStreamingAnswer preserves inline math markup during streaming', () => {
  installMinimalDom()
  const input = '容量衰减满足 $Q_{loss} = k x^2$。'

  const html = formatStreamingAnswer(input)

  assert.match(html, /<sub>loss<\/sub>/i)
  assert.match(html, /<sup>2<\/sup>/i)
})

test('formatStreamingAnswer does not render malformed DOI underscores as subscripts', () => {
  installMinimalDom()
  const input = '参考 doi:10.10881742-6596_25841_012046) 的实验设置。'

  const html = formatStreamingAnswer(input)

  assert.match(html, /10\.10881742-6596_25841_012046\)/i)
  assert.doesNotMatch(html, /<sub>/i)
})

test('formatAnswer does not treat raw DOI fragments as math markup and keeps them linkable', () => {
  installMinimalDom()
  const input = '补充参考 doi:10.1039c2jm15273h 和 doi:10.1016\/S0378-7753(03)00297-0。'

  const html = formatAnswer(input)

  assert.match(html, /class="doi-link"/i)
  assert.match(html, /data-doi="10\.1039\/c2jm15273h"/i)
  assert.match(html, /data-doi="10\.1016\/S0378-7753\(03\)00297-0"/i)
  assert.doesNotMatch(html, /<sub>|<sup>/i)
})

test('formatAnswer still renders ordinary numeric math starting with 10 dot as superscript', () => {
  installMinimalDom()
  const input = '倍率关系可写作 $10.2^3$。'

  const html = formatAnswer(input)

  assert.match(html, /10\.2<sup>3<\/sup>/i)
})

test('formatAnswer renders bare DOI links inside markdown table cells', () => {
  installMinimalDom()
  const input = [
    '| DOI |',
    '| --- |',
    '| 10.1016/j.est.2024.113859 |',
  ].join('\n')

  const html = formatAnswer(input)

  assert.match(html, /<table>/i)
  assert.match(html, /class="doi-link"/i)
  assert.match(html, /data-doi="10\.1016\/j\.est\.2024\.113859"/i)
})

test('formatAnswer renders prefixed doi links inside markdown table cells', () => {
  installMinimalDom()
  const input = [
    '| DOI |',
    '| --- |',
    '| doi:10.1039c2jm15273h |',
  ].join('\n')

  const html = formatAnswer(input)

  assert.match(html, /<table>/i)
  assert.match(html, /class="doi-link"/i)
  assert.match(html, /data-doi="10\.1039\/c2jm15273h"/i)
})

test('formatAnswer does not linkify ordinary numeric prose that only resembles an implicit DOI', () => {
  installMinimalDom()
  const input = '平台电压约为 10.2V，样品编号 10.20abc 也不应被识别为 DOI。'

  const streamingHtml = formatStreamingAnswer(input)
  const finalHtml = formatAnswer(input)

  for (const html of [streamingHtml, finalHtml]) {
    assert.doesNotMatch(html, /class="doi-link"/i)
    assert.match(html, /10\.2V/)
    assert.match(html, /10\.20abc/)
  }
})

test('compare markdown keeps document subheadings and chapter blocks in order', () => {
  installMinimalDom()
  const input = [
    '### 文献 1：厚电极液相传质',
    '',
    '## 研究目的和背景',
    '- 评估高面容量下的浓差极化。',
    '',
    '## 研究方法/实验设计',
    '- 结合 XRD 与 TOF-SIMS 进行验证。',
    '',
    '### 文献 2：界面演化与倍率响应',
    '',
    '## 研究目的和背景',
    '- 比较不同界面状态下的动力学差异。',
    '',
    '## 研究方法/实验设计',
    '- 通过 OCV 与对称电池结果交叉验证。',
    '',
    '## 相同点',
    '- 两篇文献都强调离子输运约束。',
  ].join('\n')

  const streamingHtml = formatStreamingAnswer(input)
  const finalHtml = formatAnswer(input)

  for (const html of [streamingHtml, finalHtml]) {
    assert.doesNotMatch(html, /### 文献 1：厚电极液相传质/)
    assert.doesNotMatch(html, /## 研究目的和背景/)
    assert.match(html, /<h3>文献 1：厚电极液相传质<\/h3>/)
    assert.match(html, /<h3>文献 2：界面演化与倍率响应<\/h3>/)
    assert.match(html, /<h2>研究目的和背景<\/h2>/)
    assert.match(html, /<h2>研究方法\/实验设计<\/h2>/)
    assert.match(html, /<h2>相同点<\/h2>/)

    const doc1Index = html.indexOf('<h3>文献 1：厚电极液相传质</h3>')
    const doc1BackgroundIndex = html.indexOf('<h2>研究目的和背景</h2>', doc1Index)
    const doc2Index = html.indexOf('<h3>文献 2：界面演化与倍率响应</h3>', doc1BackgroundIndex)
    const doc2BackgroundIndex = html.indexOf('<h2>研究目的和背景</h2>', doc2Index)
    const compareIndex = html.indexOf('<h2>相同点</h2>', doc2BackgroundIndex)

    assert.notEqual(doc1Index, -1)
    assert.notEqual(doc1BackgroundIndex, -1)
    assert.notEqual(doc2Index, -1)
    assert.notEqual(doc2BackgroundIndex, -1)
    assert.notEqual(compareIndex, -1)
    assert.ok(doc1Index < doc1BackgroundIndex)
    assert.ok(doc1BackgroundIndex < doc2Index)
    assert.ok(doc2Index < doc2BackgroundIndex)
    assert.ok(doc2BackgroundIndex < compareIndex)
  }
})

test('compare markdown renders the new five-section patent compare structure', () => {
  installMinimalDom()
  const input = [
    '## 具体内容对比',
    '',
    '### 文献 #1 核心内容（根据PDF原文）',
    '- 文件：paper-a.pdf',
    '- Results A show 15% efficiency improvement.',
    '',
    '### 文献 #2 核心内容（根据PDF原文）',
    '- 文件：paper-b.pdf',
    '- Results B keep 200-cycle retention.',
    '',
    '## 研究方法差异',
    '',
    '### 文献 #1 采用的研究方法',
    '- XRD 与电化学测试联合验证。',
    '',
    '### 文献 #2 采用的研究方法',
    '- 循环保持结果与 OCV 平台交叉验证。',
    '',
    '## 应用领域差异',
    '',
    '### 文献 #1 关注的应用领域',
    '- 高倍率正极优化。',
    '',
    '### 文献 #2 关注的应用领域',
    '- 长循环稳定性提升。',
    '',
    '## 相同点',
    '- 两篇文献都提供了明确的实验结果。',
    '',
    '## 总结',
    '- 两篇文献分别代表效率导向与稳定性导向。',
  ].join('\n')

  const streamingHtml = formatStreamingAnswer(input)
  const finalHtml = formatAnswer(input)

  for (const html of [streamingHtml, finalHtml]) {
    assert.match(html, /<h2>具体内容对比<\/h2>/)
    assert.match(html, /<h2>研究方法差异<\/h2>/)
    assert.match(html, /<h2>应用领域差异<\/h2>/)
    assert.match(html, /<h2>相同点<\/h2>/)
    assert.match(html, /<h2>总结<\/h2>/)
    assert.match(html, /<h3>文献 #1 核心内容（根据PDF原文）<\/h3>/)
    assert.match(html, /<h3>文献 #2 核心内容（根据PDF原文）<\/h3>/)
    assert.match(html, /<h3>文献 #1 采用的研究方法<\/h3>/)
    assert.match(html, /<h3>文献 #2 采用的研究方法<\/h3>/)
    assert.match(html, /<h3>文献 #1 关注的应用领域<\/h3>/)
    assert.match(html, /<h3>文献 #2 关注的应用领域<\/h3>/)
    assert.match(html, /paper-a\.pdf/)
    assert.match(html, /paper-b\.pdf/)

    const contentIndex = html.indexOf('<h2>具体内容对比</h2>')
    const methodIndex = html.indexOf('<h2>研究方法差异</h2>')
    const applicationIndex = html.indexOf('<h2>应用领域差异</h2>')
    const sameIndex = html.indexOf('<h2>相同点</h2>')
    const summaryIndex = html.indexOf('<h2>总结</h2>')

    assert.ok(contentIndex < methodIndex)
    assert.ok(methodIndex < applicationIndex)
    assert.ok(applicationIndex < sameIndex)
    assert.ok(sameIndex < summaryIndex)
  }
})
