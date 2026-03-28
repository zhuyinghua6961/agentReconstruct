# 2026-03-25 P4-2 引用定位与证据预览能力 Spec

## 1. 结论先行

第一版可行边界已经明确：
- 做 `evidence preview`
- 有稳定页码时支持 `PDF 跳页`
- 暂不做句子级精确高亮

原因不是前端没入口，而是后端当前没有把稳定的 `locator + evidence payload` 送到前端。

---

## 2. 当前实现事实

### 2.1 前端已具备的能力

前端现状不是空白：
- 点击回答中的 DOI，会进入 `PdfReader`
- `PdfReader` 已支持通过 `locations[0].page` 拼接 `#page=` 做跳页
- 右侧面板已经能展示位置提示卡片
- 独立的引用面板已经支持 DOI 列表、参考文献预览、文献详情加载

结论：
- 第一版不需要重做前端主入口
- 现有入口可以承接 `evidence preview`
- 如果后端能稳定返回 `doi_locations`，前端也已经能消费页码跳转

### 2.2 fastQA 当前的真实状态

`fastQA` 现状：
- `reference_objects` 允许保留字典扩展字段，当前不会被强制裁平
- Stage4 已经有从 `pdf_chunks` 里构造 `sample_text` 的逻辑
- 但最终对外响应里，`doi_locations` 仍然固定为空

结论：
- `fastQA` 第一版最容易落地
- 不补索引也能先做“证据片段预览”
- 页码定位是否可用，取决于 `pdf_chunks` 上游是否已有页码字段并被继续保留

### 2.3 highThinkingQA 当前的真实状态

`highThinkingQA` 现状：
- 检索对象 `RetrievedChunk` 只稳定暴露：`text/doi/title/section_name/chunk_index/distance`
- 注释声称 chunk 带 `page_range`，但真实 `to_metadata()` 没有写入 `page_range`
- 最终对外响应里，`doi_locations` 也是固定空数组

结论：
- `highThinkingQA` 当前能稳定做的是 `evidence preview`
- 不能承诺稳定页内定位
- 如果要做跳页，必须先补索引 metadata 与 retriever 暴露字段

---

## 3. 第一版推荐边界

### 3.1 第一版要交付什么

第一版目标：
1. 点击引用先看到证据片段
2. 如果该引用带有页码，则提供“查看原页”能力
3. 如果没有页码，也必须能正常查看证据片段，不允许前端崩溃或 shape 漂移

### 3.2 第一版不要做什么

第一版不做：
- 句子级精确高亮
- PDF 页内文本 span 匹配
- 存量索引的全面重建
- viewer 级复杂 annotation 系统

---

## 4. 第一版统一 contract

建议把两条链路统一到 richer `reference_objects`，最小字段如下：

```json
{
  "doi": "10.xxxx/xxxx",
  "title": "",
  "section_name": "",
  "chunk_index": 0,
  "evidence_text": "",
  "sample_text": "",
  "page": null,
  "page_range": null,
  "locator_confidence": "none"
}
```

说明：
- `evidence_text`：第一版前端证据预览主字段
- `sample_text`：兼容已有 fastQA 字段，可逐步并到 `evidence_text`
- `page/page_range`：有就传，没有传空值，不允许结构漂移
- `locator_confidence`：取值建议 `page | section | none`

---

## 5. 两条链路的实施策略

### 5.1 fastQA

第一版策略：
- 继续沿用 `reference_objects`
- 把已有 `sample_text` 提升为稳定 contract
- 如上游 `pdf_chunks` 中存在页码，则补 `doi_locations`
- 没页码时至少保证 evidence preview 完整可看

### 5.2 highThinkingQA

第一版策略：
- 在最终 done payload 中新增 richer `reference_objects`
- 先基于已检索到的 `RetrievedChunk` 构造 `evidence_text`
- 先传 `section_name/chunk_index`
- `page/page_range` 暂时允许为空

注意：
- `highThinkingQA` 当前不应该为了第一版去补大规模索引重建
- 先把证据预览能力补齐，再决定是否进入 metadata 增强

---

## 6. 前端落点

推荐复用现有两个入口：

### 6.1 回答区 DOI 点击
- 继续打开 `PdfReader`
- 但在传入 `locations` 之外，再让阅读器可消费 `evidence preview`
- 如果没有页码，也能展示证据片段侧栏

### 6.2 引用面板
- 保留当前 DOI 列表
- 增加 evidence preview 详情区域
- 优先展示后端直接返回的 `reference_objects`
- `reference_preview` API 继续承担补充元信息，而不是唯一证据来源

---

## 7. 风险判断

### 7.1 当前最大的真实风险
不是前端交互，而是后端 shape 不统一：
- `fastQA` 已经有部分 evidence 数据，但没有稳定对外 contract
- `highThinkingQA` 证据与 locator 数据更弱
- 两边都把 `doi_locations` 留空，导致现有跳页入口无法真正工作

### 7.2 第二阶段才需要解决的问题
- `page_range` 真正入索引
- `RetrievedChunk` 暴露页码
- 句子/段落 span 对齐
- viewer 内精确高亮

---

## 8. 验收标准

第一版完成标准：
1. `fastQA` 与 `highThinkingQA` 都输出稳定的 richer `reference_objects`
2. 前端点击引用时能看到证据片段
3. 有页码的引用能跳页
4. 无页码的引用也能正常展示预览
5. 不破坏现有 `reference_links/pdf_links/references` 兼容行为

---

## 9. 后续建议

建议顺序：
1. 先做 richer reference contract 与 evidence preview
2. 再补 `fastQA` 的 `doi_locations`
3. 再评估 `highThinkingQA` 的 metadata 增强
4. 最后再决定是否做句子级高亮
