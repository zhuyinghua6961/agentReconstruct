# 2026-03-28 检索提速与配额管理待办

## 文档目的

本文记录两类后续待办：

1. `fastQA` / `highThinkingQA` 检索与问答流程提速
2. 配额管理范围补齐与配置收口

这是一份独立待办清单，不替代现有优先级路线图，主要用于后续持续跟进。

---

## 一、检索与问答提速待办

### T1 `fastQA` 检索流程性能审计

#### 目标
- 拆开确认 `stage1 / stage2 / stage2.5 / stage3 / stage4` 的真实耗时占比
- 明确冷启动、缓存未命中、重排序、PDF sentence 对齐、后裁剪各自的耗时贡献

#### 待做项
- 补齐 `fastQA` 各阶段的统一耗时日志口径
- 对真实请求样本统计 `P50 / P90 / P95`
- 区分“首 chunks 时间”与“总完成时间”
- 单独统计 `rerank`、后裁剪、`stage2.5` 的耗时
- 输出一份 `fastQA latency breakdown` 文档

#### 关注点
- 不只看总耗时，要看哪一步在拖首 chunk
- 不只看 warm path，要看冷启动路径

---

### T2 `fastQA` 检索链路优化设计

#### 目标
- 在不破坏对齐度和引用质量的前提下，缩短首 chunks 时间和总响应时间

#### 待做项
- 评估 `stage2` 检索候选量是否过大
- 评估 `rerank` 是否可以做更轻量的前置筛选
- 评估 `stage2.5` sentence 对齐是否存在重复扫描或无效扩展
- 评估 `PDF chunks` 物化 / 预热 / 缓存是否还能前移
- 评估 `stage4` 前是否存在不必要的同步等待

#### 交付物
- 一份 `fastQA retrieval optimization spec`
- 一份按收益排序的优化列表

---

### T3 `highThinkingQA` Step1 / 检索 / Checker 全链路慢点审计

#### 目标
- 明确 `highThinkingQA` 慢点到底落在：
  - `direct_answer`
  - `decompose`
  - 预回答
  - retrieval
  - synthesis
  - checker

#### 待做项
- 把以下时间点统一打平统计：
  - `step1 direct_answer start`
  - `step1 direct_answer done`
  - `step1 waiting for direct answer after retrieval pipeline`
  - `step3 retrieval done`
  - `step4 synthesis first_chunk`
  - `checker start`
  - `checker done`
  - `done event`
- 输出真实请求样本统计
- 单独分析 `direct_answer` 对首 chunks 时间的拖累程度
- 单独分析 `checker` 超时与后台残留执行问题

#### 关注点
- `direct_answer` 不是只看总耗时，要看它是否卡住 `step4`
- `checker` 不只是慢，还要看超时策略是否真正生效

---

### T4 `highThinkingQA` direct answer 优化

#### 当前已知结论
- `direct_answer` prompt 本身体量不大
- 当前更像是模型选择、输出长度、阶段等待方式导致慢，而不是 prompt 过大

#### 待做项
- 统计 `direct_answer` 首 content chunk 时间
- 统计 `direct_answer` 完整完成时间
- 评估 `DIRECT_ANSWER_MODEL` 是否需要与其他阶段分模
- 评估 `max_tokens` 是否过大
- 评估是否应把 `direct_answer` 从“阻塞 step4”改成“软依赖”
- 评估 `direct_answer` 缓存命中率与 TTL 是否合适

#### 输出要求
- 单独形成 `highThinkingQA direct_answer latency spec`

---

### T5 检索缓存与预热策略统一审查

#### 目标
- 系统级提升命中率，减少重复检索和重复文件物化

#### 待做项
- 盘清 `fastQA` 当前问答阶段缓存是否全部真实生效
- 盘清 `highThinkingQA` 当前缓存命中口径与日志口径
- 审查是否需要增加：
  - 检索结果缓存
  - 引用对齐结果缓存
  - `PDF text / parsed text / workbook profile` 预热
- 评估 `public-service` 是否需要补更多预热信号

---

## 二、配额管理待办

### T6 问答主链配额真正接入

#### 当前状态
- [done] `gateway` 已作为问答主链配额编排器接入
- `ask_query` / `file_qa` 已按实际路由分类接入 `gateway -> public-service` internal grant contract
- 同步与流式问答均已改为“成功结果才 finalize 计额”
- `patent` 仍明确不纳入本轮

#### 待做项
- 给 `gateway -> fastQA / highThinkingQA` 的 ask / ask_stream 主链设计问答配额方案
- 明确问答配额应该挂在：
  - `gateway` 统一前置
  - 还是各 QA backend 自己校验
- 明确普通 QA / 文件 QA / 混合 QA / thinking QA 是否共用同一 `quota_type`
- 明确成功记账时机：
  - 请求进入即记
  - 首 chunk 后记
  - done 后记

#### 建议优先级
- 高

---

### T7 文档辅助接口配额补齐

#### 当前状态
- [done] 以下接口已统一接入 `doc_assist`
  - `summarize_pdf`
  - `translate`
  - `extract_pdf_text`
  - `literature_content`
  - `reference_preview`
- [done] 已认证请求走 strict config
- [done] 匿名兼容调用继续可用，但不消耗用户配额

#### 待做项
- 评估这些接口是否需要独立配额类型
- 或是否挂到统一文献辅助配额下
- 明确 strict / non-strict 策略
- 明确前端对应错误提示文案

---

### T8 上传与导入配额类型收口

#### 当前状态
- [done] 会话绑定上传已退出用户可见 quota model，不再计入 `file_upload`
- [partial] 管理员批量导入仍保留 `excel_upload` 作为兼容内部行为
- [done] 前端 canonical UI 不再暴露 `file_upload` / `excel_upload`

#### 待做项
- 盘清配额类型命名是否需要统一
- 决定是否保留：
  - `file_upload`
  - `excel_upload`
  - 或拆成更明确的前后端展示名称
- 调整前端管理员页面说明，避免歧义

---

### T9 配额管理后台与活链路一致性审计

#### 目标
- 确保“前端能配”的配额，后端真的在消费
- 确保“后端在消费”的配额，前端也能看见和配置

#### 待做项
- 建一份配额矩阵：
  - `quota_type`
  - 消费接口
  - strict / non-strict
  - 是否前端可配置
  - 是否用户侧可见
- 排查“可配但未使用”的 quota_type
- 排查“已使用但前端未覆盖”的 quota_type

#### 当前已知重点
- [done] `ask_query`：已接入普通 QA 主链
- [done] `file_qa`：已接入 PDF / 表格 / 混合 QA 主链
- [done] `file_view`：原文查看与会话文件下载已接入
- [done] `doc_assist`：文档辅助接口已接入
- [partial] `excel_upload`：仍是兼容内部类型，未进入 canonical UI

---

### T10 配额记账时机与失败语义统一

#### 目标
- 统一上传、文档接口、问答接口的配额记账规则

#### 待做项
- [done] QA 主链：成功响应后 finalize，失败不扣
- [done] 文档辅助：认证态 strict，成功响应后 finalize
- [done] 文件查看：成功响应后 finalize
- [done] finalize 失败：成功业务结果保留成功，并附带 `quota_counted=false` / warning
- [open] 仍需补一轮人工 smoke，确认所有前后端提示文案与管理后台展示完全一致

---

## 三、建议执行顺序

1. `T3 highThinkingQA 全链路慢点审计`
2. `T4 highThinkingQA direct answer 优化`
3. `T1 fastQA 检索流程性能审计`
4. `T2 fastQA 检索链路优化设计`
5. `T6 问答主链配额真正接入`
6. `T9 配额管理后台与活链路一致性审计`
7. `T7 / T8 / T10` 按配额方案收口

---

## 四、完成标记约定

- `[open]` 尚未开始
- `[doing]` 正在进行
- `[done]` 已完成并有对应文档或实现支撑

当前状态：
- `T1 [open]`
- `T2 [open]`
- `T3 [open]`
- `T4 [open]`
- `T5 [open]`
- `T6 [done]`
- `T7 [done]`
- `T8 [doing]`
- `T9 [doing]`
- `T10 [doing]`
