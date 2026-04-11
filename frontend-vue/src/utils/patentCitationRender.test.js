import test from 'node:test'
import assert from 'node:assert/strict'

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

async function loadRenderUtils() {
  installMinimalDocumentStub()
  return await import('./index.js')
}

test('legacy patent citations stay clickable without exposing raw patent_id text', async () => {
  const { formatAnswer, formatStreamingAnswer } = await loadRenderUtils()
  const input = '结论成立 (patent_id=CN100420075C)'

  const finalHtml = formatAnswer(input)
  const streamingHtml = formatStreamingAnswer(input)

  for (const html of [finalHtml, streamingHtml]) {
    assert.match(html, /class="doi-link patent-link"/)
    assert.match(html, /data-patent-id="CN100420075C"/)
    assert.match(html, />CN100420075C</)
    assert.doesNotMatch(html, />patent_id=CN100420075C</)
  }
})

test('readable patent citations are linkified in new-path rendering', async () => {
  const { formatAnswer, formatStreamingAnswer } = await loadRenderUtils()
  const input = '可参考专利号 CN118645714A，并结合 (CN115692635A) 的电压窗口。'

  const finalHtml = formatAnswer(input)
  const streamingHtml = formatStreamingAnswer(input)

  for (const html of [finalHtml, streamingHtml]) {
    assert.match(html, /class="doi-link patent-link"/)
    assert.match(html, /data-patent-id="CN118645714A"/)
    assert.match(html, /data-patent-id="CN115692635A"/)
    assert.match(html, />CN118645714A<\/a>/)
    assert.match(html, />CN115692635A<\/a>/)
    assert.doesNotMatch(html, /patent_id=/)
  }
})

test('plain inline patent ids are linkified and duplicate trailing patent citations are removed', async () => {
  const { formatAnswer, formatStreamingAnswer } = await loadRenderUtils()
  const input = [
    '双颗粒级配：CN109192948B 使用球形小颗粒填充大颗粒空隙，压实密度达 2.69–2.72 g/cm³（实施例1–3），同时1C放电比容量为149–150 mAh/g (CN109192948B)。',
    '- 多粒度混合：CN113562714A 将大颗粒磷酸铁（0.6–3 μm）与小颗粒（0.05–0.3 μm）按质量比5–50%混合，极片压实密度达 2.83–2.87 g/cm³（实施例1），且1C容量＞150 mAh/g (CN113562714A)。',
    '- 数学模型优化：CN115863630B 通过公式 a = 0.315A - 0.086B - 0.25 计算混合比例，使混合材料压实密度拟合值达 2.84–2.95 g/cm³，与实际值误差＜0.01 g/cm³ (CN115863630B)。',
  ].join('\n')

  const finalHtml = formatAnswer(input)
  const streamingHtml = formatStreamingAnswer(input)

  for (const html of [finalHtml, streamingHtml]) {
    assert.match(html, /data-patent-id="CN109192948B"/)
    assert.match(html, /data-patent-id="CN113562714A"/)
    assert.match(html, /data-patent-id="CN115863630B"/)
    assert.equal((html.match(/data-patent-id="CN109192948B"/g) || []).length, 1)
    assert.equal((html.match(/data-patent-id="CN113562714A"/g) || []).length, 1)
    assert.equal((html.match(/data-patent-id="CN115863630B"/g) || []).length, 1)
    assert.doesNotMatch(html, /\(.*CN109192948B.*\)/)
    assert.doesNotMatch(html, /\(.*CN113562714A.*\)/)
    assert.doesNotMatch(html, /\(.*CN115863630B.*\)/)
  }
})

test('plain inline patent ids stay clickable when immediately followed by Chinese text', async () => {
  const { formatAnswer, formatStreamingAnswer } = await loadRenderUtils()
  const input = '双颗粒级配：CN109192948B使用球形小颗粒填充大颗粒空隙，且 CN113562714A的压实密度更高。'

  const finalHtml = formatAnswer(input)
  const streamingHtml = formatStreamingAnswer(input)

  for (const html of [finalHtml, streamingHtml]) {
    assert.match(html, /data-patent-id="CN109192948B"/)
    assert.match(html, /data-patent-id="CN113562714A"/)
    assert.match(html, />CN109192948B<\/a>使用球形小颗粒/)
    assert.match(html, />CN113562714A<\/a>的压实密度/)
  }
})

test('raw patent urls are not broken by inline patent linkification', async () => {
  const { formatAnswer, formatStreamingAnswer } = await loadRenderUtils()
  const input = '原始链接：https://patents.google.com/patent/CN109192948B/en'

  const finalHtml = formatAnswer(input)
  const streamingHtml = formatStreamingAnswer(input)

  for (const html of [finalHtml, streamingHtml]) {
    assert.match(html, /https:\/\/patents\.google\.com\/patent\/CN109192948B\/en/)
    assert.doesNotMatch(html, /data-patent-id="CN109192948B"/)
  }
})

test('trailing patent citation is preserved when the same patent id only appears inside a raw url', async () => {
  const { formatAnswer, formatStreamingAnswer } = await loadRenderUtils()
  const input = '原始链接：https://patents.google.com/patent/CN109192948B/en (CN109192948B)。'

  const finalHtml = formatAnswer(input)
  const streamingHtml = formatStreamingAnswer(input)

  assert.match(finalHtml, /https:\/\/patents\.google\.com\/patent\/CN109192948B\/en/)
  assert.match(finalHtml, /data-patent-id="CN109192948B"/)
  assert.match(finalHtml, /https:\/\/patents\.google\.com\/patent\/CN109192948B\/en"/)
  assert.doesNotMatch(finalHtml, /en%E3%80%82|CN109192948B\/en%E3%80%82/)
  assert.match(finalHtml, /\(<a href="#" class="doi-link patent-link" data-patent-id="CN109192948B">CN109192948B<\/a>\)。/)

  assert.match(streamingHtml, /https:\/\/patents\.google\.com\/patent\/CN109192948B\/en/)
  assert.match(streamingHtml, /data-patent-id="CN109192948B"/)
  assert.match(streamingHtml, /\(<a href="#" class="doi-link patent-link" data-patent-id="CN109192948B">CN109192948B<\/a>\)。/)
})

test('trailing patent citation is preserved when the patent id does not already appear earlier in the clause', async () => {
  const { formatAnswer, formatStreamingAnswer } = await loadRenderUtils()
  const input = '可以进一步参考其电压窗口说明 (CN115692635A)。'

  const finalHtml = formatAnswer(input)
  const streamingHtml = formatStreamingAnswer(input)

  for (const html of [finalHtml, streamingHtml]) {
    assert.match(html, /data-patent-id="CN115692635A"/)
    assert.match(html, /<a href="#" class="doi-link patent-link" data-patent-id="CN115692635A">CN115692635A<\/a>/)
  }
})

test('trailing patent citation is preserved when it differs from the inline patent id in the same clause', async () => {
  const { formatAnswer, formatStreamingAnswer } = await loadRenderUtils()
  const input = '对比 CN109192948B 的压实密度窗口时，也可参考边界方案 (CN115692635A)。'

  const finalHtml = formatAnswer(input)
  const streamingHtml = formatStreamingAnswer(input)

  for (const html of [finalHtml, streamingHtml]) {
    assert.equal((html.match(/data-patent-id="CN109192948B"/g) || []).length, 1)
    assert.equal((html.match(/data-patent-id="CN115692635A"/g) || []).length, 1)
  }
})

test('bare patent ids inside inline code and code fences are not linkified', async () => {
  const { formatAnswer, formatStreamingAnswer } = await loadRenderUtils()
  const inlineCodeInput = '`CN109192948B`'
  const fencedCodeInput = '```\nCN109192948B\n```'

  const outputs = [
    formatAnswer(inlineCodeInput),
    formatStreamingAnswer(inlineCodeInput),
    formatAnswer(fencedCodeInput),
    formatStreamingAnswer(fencedCodeInput),
  ]

  for (const html of outputs) {
    assert.doesNotMatch(html, /data-patent-id="CN109192948B"/)
  }

  assert.match(formatAnswer(inlineCodeInput), /<code>CN109192948B<\/code>/)
  assert.match(formatStreamingAnswer(inlineCodeInput), /<code>CN109192948B<\/code>/)
  assert.match(formatAnswer(fencedCodeInput), /<pre><code>CN109192948B[\s\S]*<\/code><\/pre>/)
})

test('trailing patent citation is preserved when the earlier patent id only appears inside inline code', async () => {
  const { formatAnswer, formatStreamingAnswer } = await loadRenderUtils()
  const input = '示例：`CN109192948B` (CN109192948B)。'

  const finalHtml = formatAnswer(input)
  const streamingHtml = formatStreamingAnswer(input)

  for (const html of [finalHtml, streamingHtml]) {
    assert.match(html, /<code>CN109192948B<\/code>/)
    assert.match(html, /data-patent-id="CN109192948B"/)
    assert.match(html, /\(<a href="#" class="doi-link patent-link" data-patent-id="CN109192948B">CN109192948B<\/a>\)。/)
  }
})

test('inline bullet patterns after a clause are normalized into a real list', async () => {
  const { formatAnswer, formatStreamingAnswer } = await loadRenderUtils()
  const input = '结论如下：- 充电上限约 3.65V - 放电下限约 2.5V'

  const finalHtml = formatAnswer(input)
  const streamingHtml = formatStreamingAnswer(input)

  for (const html of [finalHtml, streamingHtml]) {
    assert.match(html, /结论如下：/)
    assert.match(html, /<li>充电上限约 3\.65V<\/li>/)
    assert.match(html, /<li>放电下限约 2\.5V<\/li>/)
    assert.doesNotMatch(html, /：- 充电上限/)
  }
})

test('single inline bullet is normalized early during streaming', async () => {
  const { formatAnswer, formatStreamingAnswer } = await loadRenderUtils()
  const input = '结论如下：- 充电上限约 3.65V'

  const finalHtml = formatAnswer(input)
  const streamingHtml = formatStreamingAnswer(input)

  for (const html of [finalHtml, streamingHtml]) {
    assert.match(html, /结论如下：/)
    assert.match(html, /<li>充电上限约 3\.65V<\/li>/)
    assert.doesNotMatch(html, /：- 充电上限/)
  }
})

test('ordinary hyphenated text is not mistaken for an inline list', async () => {
  const { formatAnswer, formatStreamingAnswer } = await loadRenderUtils()
  const input = '该路线属于高电压-高密度体系，电压范围：-20 至 30 mV 波动。'

  const finalHtml = formatAnswer(input)
  const streamingHtml = formatStreamingAnswer(input)

  for (const html of [finalHtml, streamingHtml]) {
    assert.doesNotMatch(html, /<li>/)
    assert.match(html, /高电压-高密度体系/)
    assert.match(html, /-20 至 30 mV 波动/)
  }
})

test('inline list normalization does not split ordinary item separators inside one bullet', async () => {
  const { formatAnswer, formatStreamingAnswer } = await loadRenderUtils()
  const input = '结论如下：- 电压窗口 A - B 段更稳定'

  const finalHtml = formatAnswer(input)
  const streamingHtml = formatStreamingAnswer(input)

  for (const html of [finalHtml, streamingHtml]) {
    assert.match(html, /<li>电压窗口 A - B 段更稳定<\/li>/)
    assert.doesNotMatch(html, /<li>B 段更稳定<\/li>/)
  }
})
