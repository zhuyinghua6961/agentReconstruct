# fastQA Stage4 DOI Repair 优化 Spec

本文只覆盖 `fastQA` 普通问答 `stage4` 的程序化 DOI 修复尾耗时问题，不讨论 `gateway`、`public-service`、文件问答、混合问答。

对照基线：

- 当前实现：`/home/cqy/worktrees/highThinking/fastQA/app/modules/generation_pipeline/`
- 旧版参考：`/home/cqy/worktrees/fastapi-version/backend/app/modules/generation_pipeline/`

## 1. 问题现象

当前 `fastQA` 在前端上表现为：

1. `stage4` 正文已经生成完成，但前端仍然停留在“思考中”或看起来像卡住。
2. 最终 `done` 事件返回明显偏晚，导致用户误以为答案没有结束。
3. DOI 插入成功率偏低时，尾部耗时会更明显。

这不是 LLM 主体流式输出慢，而是 **LLM 流结束后的同步尾处理过重**。

## 2. 当前调用链

普通问答 `stage4` 的关键路径是：

1. `synthesis_streaming.py::iter_stage4_synthesis_with_pdf_chunks()`
2. LLM 逐 chunk 流式输出正文
3. `stage4 llm stream completed`
4. `_validate_answer()` 清理无效 DOI
5. `programmatic_insert_dois_fn(...)`
6. 再次 `_validate_answer()`
7. `build_references_from_pdf_chunks_fn(...)`
8. 输出最终 `done`

关键点：

- 前端看到的正文 chunk 已经发出去了。
- 但 `done` 之前还要跑 DOI 修复、校验、reference 构建。
- 其中最重的是 `doi_inserter.py::programmatic_insert_dois()`。

## 3. 已确认根因

### 3.1 已修复的一层问题

之前 `context_loading.py::load_pdf_sentences()` 没有真正接线，导致 DOI 修复阶段虽然在跑“PDF 句子验证”，但实际拿不到 PDF 句子。

现状：

- 已接到 `get_settings().papers_dir`
- 已通过 `ensure_local_paper_pdf()` 读取本地 PDF
- 已用 `fitz` 提取句子
- 已加 `lru_cache`

这一步解决的是“验证依据缺失”，不是最终的尾耗时问题。

### 3.2 当前剩余热点

`doi_inserter.py` 还有两个主要性能热点。

#### 热点 A：候选 DOI 预处理过重

当前代码在构建 `candidate_docs` 时，会对每一个候选 DOI 立即执行：

1. `agent._load_pdf_sentences(doi_clean)`

问题：

- 即使这个 DOI 最终不会成为任一句子的最佳候选，也会提前加载 PDF 句子。
- 检索结果较多时，会出现大量无效 PDF 打开、文本提取与切句。

#### 热点 B：embedding 重复编码严重

当前对每个答案句子、每个候选文档，都会重复做：

1. `emb_model.encode([sent_strip])`
2. `emb_model.encode([doc_text])`

进入验证阶段后，又会重复做一轮：

1. `emb_model.encode([sent_strip])`
2. `emb_model.encode([doc_text])`
3. 对若干 `pdf_sentences` 再逐条 `encode`

问题：

- 同一个答案句子 embedding 被重复算很多次。
- 同一个 doc 文本 embedding 也被重复算很多次。
- 同一个 PDF 句子在多句答案上也会被反复算。

这会直接拖慢 `stage4 programmatic DOI repair`。

## 4. 当前语义约束

这次优化不能改变以下行为：

1. 仍然按句子级别寻找最佳 DOI。
2. 仍然保留现有阈值：
   - `insert_similarity_threshold`
   - `insert_seq_verify_threshold`
   - `insert_embed_verify_threshold`
   - `insert_vector_verify_threshold`
3. 仍然保留 `require_pdf_evidence_for_doi` 分支。
4. 仍然保留 `_alignment_audit` 写入。
5. 仍然保留 `strict_mode` 的后验验证。

也就是说，这次优先做 **等价优化**，不是改算法语义。

## 5. 修复方案

### 5.1 第一阶段：低风险等价优化

只动 `doi_inserter.py`。

#### 改动 1：PDF 句子延迟加载

把当前：

- 构建 `candidate_docs` 时就加载 `pdf_sentences`

改成：

- `candidate_docs` 里先只保存 DOI、doc_text、vector_sim
- 只有在某个 `best_doc` 进入验证阶段时，才按 DOI 延迟加载 `pdf_sentences`
- 同一个 DOI 只加载一次，本次函数调用内复用缓存

目标：

- 避免为大量未命中的候选 DOI 提前做 PDF IO 和切句

#### 改动 2：embedding 局部缓存

在单次 `programmatic_insert_dois()` 调用内增加缓存：

- `sentence_embedding_cache[sentence_text]`
- `doc_embedding_cache[doc_text]`
- `pdf_sentence_embedding_cache[pdf_sentence_text]`

目标：

- 同一句答案只编码一次
- 同一段检索片段只编码一次
- 同一条 PDF 句子只编码一次

#### 改动 3：避免同一轮重复取 `emb_model`

把：

- 每次循环里重复 `getattr(agent.literature_expert, "embedding_model", None)`

收敛成函数级单次读取。

这项收益不大，但可以让逻辑更稳。

### 5.2 第二阶段：日志补强

为定位尾耗时，需要补充 `doi_inserter.py` 的细粒度日志，至少包括：

1. 候选 DOI 数量
2. 句子数量
3. 命中的最佳 DOI 分布
4. 延迟加载 PDF 的 DOI 次数
5. embedding cache 命中情况
6. 验证通过/失败统计
7. 总耗时

注意：

- 日志必须是文本可读格式
- 不要再落成难以直接查看的二进制格式

### 5.3 当前不做的事

本轮先不做以下高风险变更：

1. 改 `strict_verify_answer()` 时机
2. 改 `done` 事件协议
3. 把 DOI repair 完全异步化
4. 放宽 `pdf evidence` 校验阈值
5. 修改前端逻辑

原因：

- 这些都可能改变最终答案、引用准确性或前端契约
- 当前优先目标是把尾耗时压下来，并恢复与旧版更接近的表现

## 6. 验收标准

### 6.1 功能验收

1. 普通问答仍能正常输出最终答案。
2. LLM 未插够 DOI 时，程序化 DOI 修复仍会触发。
3. `strict_mode`、`_alignment_audit`、reference 构建行为不回退。
4. 现有针对 `stage4` 的测试保持通过。

### 6.2 性能验收

1. `stage4 llm stream completed` 到 `stage4 programmatic DOI repair finished` 的时间明显缩短。
2. 在相同问题、相同检索命中条件下，日志中不应再看到“对大量无关 DOI 预先加载 PDF 句子”的行为。
3. 单次请求内，相同句子和相同文档不应重复编码。

### 6.3 观测点

重点查看：

- `resource/runtime/dev/fastQA/fastqa-app.log`

关注日志：

1. `stage4 llm stream completed`
2. `stage4 programmatic DOI repair triggered`
3. `stage4 programmatic DOI repair finished`
4. `句子对齐并验证通过`
5. `句子对齐但验证未通过`

## 7. 实施顺序

1. 写本 spec
2. 优化 `doi_inserter.py`
3. 补测试
4. 提权跑定向测试
5. 重启 `fastQA`
6. 看日志复核

## 8. 涉及文件

- [doi_inserter.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/generation_pipeline/doi_inserter.py)
- [context_loading.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/generation_pipeline/context_loading.py)
- [synthesis_streaming.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/generation_pipeline/synthesis_streaming.py)
- [test_generation_stage4_synthesis.py](/home/cqy/worktrees/highThinking/fastQA/tests/test_generation_stage4_synthesis.py)
- [test_context_loading.py](/home/cqy/worktrees/highThinking/fastQA/tests/test_context_loading.py)
