# Patent File/Hybrid Streaming Remediation Spec

## Goal

让 `patent` 文件问答链路的流式行为变得一致、可解释、可验证：

- `pdf_qa` 保持真实内容流式
- `tabular_qa` 不再伪装成“天然流式”
- `hybrid_qa` 不再只在最终合成后一次性切片输出
- 前后端能明确区分“步骤进度流”“来源预览流”“最终答案流”

目标不是复制 `fastQA` 代码。
目标是在不修改 `fastQA` 的前提下，把 `patent` 文件问答和混合问答的流式协议、运行行为、用户感知修到合理状态。

## Scope

In scope:

- `patent` `pdf_qa`
- `patent` `tabular_qa`
- `patent` `hybrid_qa` with:
  - `pdf+table`
  - `pdf+kb`
  - `table+kb`
  - `pdf+table+kb`
- `patent` 流式 SSE 事件协议
- 必要的 `frontend-vue` 流式渲染适配，但仅限 `patent` 文件问答链路

Out of scope:

- `fastQA` 代码改动
- `patent` 普通问答 / `kb_qa`
- `gateway` 路由选择逻辑
- 文件问答答案内容质量本身的另一轮 prompt/formatter 改造
- durable persistence 架构重构

## Hard Constraints

1. 不允许修改 `fastQA`。
2. 不允许改 `patent` 普通问答逻辑。
3. 不能通过无限加 prompt、无限加上下文来“假解决”问题。
4. 现有 `ask_stream` SSE 主协议必须保持向后兼容，至少不能把非 `patent` 模式打挂。
5. 未升级前端不得收到 `preview` 内容事件；否则会被错误追加进主回答正文。
6. standalone `patent kb_qa` 行为必须保持不变；任何 KB preview 支持都只能发生在文件/混合问答链路内。

## Current-State Findings

### 1. SSE 外壳是支持增量事件的

`AskService.stream_ask()` 已经支持把 `content_callback` 推成逐条 `content` 事件。

结论：

- 最外层协议不是瓶颈
- 真正决定是否“真流式”的，是下游服务有没有持续调用 `content_callback`

### 2. `pdf_qa` 是部分真流式

运行实测显示：

- 先发 `step`
- 完成 PDF 提取和模型首包等待后
- 开始连续发送 `content` chunk

这说明 `pdf_qa` 的内容输出阶段具备真实流式能力。

但它不是“早流式”：

- 首个内容 token 前仍有较长等待窗口
- 用户感知上容易误判为“没在流”

### 3. `tabular_qa` 是末尾切片伪流式

当前 `tabular_service` 会：

1. 先完整拿到答案
2. 再调用 `emit_text_chunks(answer, ...)`

结论：

- 它不是边生成边输出
- 只是把完整答案重新切片发给前端

### 4. `hybrid_qa` `pdf+table` 明确关闭了子分支内容流

当前 `file_routes._build_hybrid_result()` 中：

- `pdf_service.execute(..., content_callback=None)`
- `tabular_service.execute(..., content_callback=None)`

只有 PDF 和表格都结束后，才会对最终 `answer_text` 执行 `emit_text_chunks(...)`。

结论：

- `pdf+table` 完全不是增量混合流式
- 前端看到的是“最终答案切片回放”

### 5. `hybrid_qa` 任何 `+kb` 路径都会在 executor 层清空文件侧内容流

当前 `PatentExecutor._execute_file_route()` 中：

- `content_callback=None if contract.includes_kb else content_callback`

这意味着：

- 只要 `source_scope` 包含 `kb`
- 文件分支的内容流式就会被主动关闭
- 等 KB 跑完并 merge 后，才统一切片输出最终答案

### 6. 当前前端无法安全消费“来源级流式”和“最终答案流式”两种语义

现有前端对 `content` 事件的典型理解是：

- 来一个 chunk，就把它追加到当前回答正文

因此如果后端直接先流 PDF 子答案、再流表格子答案、最后再流最终合成答案，前端会把它们拼成一段重复文本。

结论：

- 仅改后端 callback 透传不够
- 如果要做真正的 hybrid 增量流式，必须扩展事件语义或前端消费策略
- 而且必须有服务端 capability gate，禁止把 `preview` 事件发给旧前端

## Problem Statement

当前 `patent` 文件问答流式问题不是单点 bug，而是三类问题叠加：

1. 行为不一致：
   - `pdf_qa` 真流式
   - `tabular_qa` 假流式
   - `hybrid_qa` 末尾流式
2. 协议语义缺失：
   - 只有一个 `content` 通道
   - 没法区分“来源预览”和“最终答案”
3. 用户感知差：
   - 前面长时间只有 step，没有正文
   - hybrid 看起来像根本没流

## Options

### Option A: 保守修复，仅做“诚实流式”

做法：

- 保持 `pdf_qa` 现状
- `tabular_qa` 和 `hybrid_qa` 不再强调内容流式，只保留 step
- 最终答案继续一次性切片

优点：

- 风险最低
- 几乎不需要前端改动

缺点：

- 不能解决 hybrid 没有增量正文的问题
- 用户体验提升有限

### Option B: 扩展 `patent` 流式协议，区分来源流和最终答案流

做法：

- 保持现有 `type=content`
- 新增可选字段，例如：
  - `content_role`: `preview` | `final`
  - `content_source`: `pdf` | `table` | `kb` | `hybrid`
- hybrid 期间允许先发来源级预览，再发最终答案流
- 前端按角色分别渲染

优点：

- 能做真正的 phased streaming
- 不需要把来源文本和最终答案硬拼在一起
- 与当前架构兼容度高

缺点：

- 需要前后端一起改
- 必须处理老前端的 capability gate，不能只靠“忽略新字段”

### Option C: 做真正的“单通道增量合成”

做法：

- 后端内部维护一个持续演化的最终答案
- 每个来源分支完成后都对“最终回答”继续改写并只发 delta

优点：

- 用户看到的永远是一个回答面板
- 理论体验最好

缺点：

- 复杂度最高
- 很容易引入重复、覆盖、回滚和引用错位
- 当前 `patent` 架构不适合直接走这条路

## Recommendation

选择 `Option B`。

理由：

- 它能解决核心问题：hybrid 终于可以在最终合成前给出正文级反馈
- 它不要求一次性推翻现有 executor/file-route/service 分层
- 它允许逐步上线：
  - 先做协议扩展和前端兼容
  - 再逐个 route 打开来源级流式

## Target Behavior

### 1. 规范性路由矩阵

下表是本 spec 的唯一规范性事件矩阵。若后文示例与此冲突，以本表为准。

| Route | Source Scope | Capability Disabled | Capability Enabled |
|------|------|------|------|
| `pdf_qa` | `pdf` | 只发传统 `content`，语义等同 `final/pdf` | 只发 `final/pdf` |
| `tabular_qa` | `table` | 只发传统 `content`，语义等同 `final/table` | 只发 `final/table` |
| `hybrid_qa` | `pdf+table` | 不发 `preview`；只在最终合成后发 `final/hybrid` | 可发 `preview/pdf`、`preview/table`，最终发 `final/hybrid` |
| `hybrid_qa` | `pdf+kb` | 不发 `preview`；只在最终合成后发 `final/hybrid` | 可发 `preview/pdf`；`preview/kb` 为可选；最终发 `final/hybrid` |
| `hybrid_qa` | `table+kb` | 不发 `preview`；只在最终合成后发 `final/hybrid` | 可发 `preview/table`；`preview/kb` 为可选；最终发 `final/hybrid` |
| `hybrid_qa` | `pdf+table+kb` | 不发 `preview`；只在最终合成后发 `final/hybrid` | 可发 `preview/pdf`、`preview/table`；`preview/kb` 为可选；最终发 `final/hybrid` |
| any file route cache hit | any in-scope scope | 只回放 `final/*` | 仍只回放 `final/*` |

约束：

- `pdf_qa` 和 `tabular_qa` 不使用 `preview`。
- `preview` 只允许出现在 `hybrid_qa` 且 capability enabled 的请求中。
- cache hit 不回放 `preview`，只回放最终答案。

### 2. `pdf_qa`

目标行为：

- 保持现有真实内容流式，正文语义为 `final/pdf`
- 在首个正文 chunk 前，继续输出 step
- 增加更清晰的“生成中”阶段信号

不要求：

- 把模型首 token 延迟降到极低

### 3. `tabular_qa`

目标行为：

- 若底层 answer builder 可迭代输出，则逐块透传 `final/table`
- 若底层只能返回完整字符串，则允许一次性或切片发出 `final/table`

换句话说：

- 允许它仍然不是“真流式”
- 但协议上必须诚实，不允许把 `tabular_qa` 的完整答案标记成 `preview`

### 4. `hybrid_qa` `pdf+table`

目标行为：

1. step: PDF 提取/生成
2. `preview` content: PDF 来源预览，可持续流
3. step: 表格执行/生成
4. `preview` content: 表格来源预览
5. step: hybrid synthesis
6. `final` content: 最终统一答案流

前端要求：

- `preview` 不直接并入最终答案正文
- 可放在“处理中证据预览”区域，或作为可折叠来源卡片
- `final` 才写入主回答正文

### 5. `hybrid_qa` `pdf+kb` / `table+kb` / `pdf+table+kb`

目标行为一致：

- 不再在 executor 层直接粗暴清空文件侧 `content_callback`
- 允许文件分支以 `preview` 形式先流
- KB 分支如果支持流式，也只能在 `hybrid_qa` 文件链路里以 `preview` 形式输出
- 最终 merge 结果以 `final` 形式输出
- standalone `patent kb_qa` 不使用本 spec 的 `preview` 协议

### 6. 缓存命中

目标行为：

- 缓存命中时允许直接回放 `final` 内容
- 不强求重建所有 `preview` 流
- 但需要在 metadata 中标记 `cache_hit=true`

## Protocol Design

现有字段保持不变：

- `type`
- `seq`
- `trace_id`
- `content`

新增可选字段：

- `content_role`
  - `final`: 主回答正文
  - `preview`: 来源级预览
- `content_source`
  - `pdf`
  - `table`
  - `kb`
  - `hybrid`
- `content_stream_id`
  - 字符串
  - 同一来源预览缓冲区的稳定标识
  - 例如：`pdf:primary`、`table:selected`、`kb:verification`
- `content_phase`
  - `start` | `delta` | `end` | `snapshot`
  - `start`: 初始化一个 preview/final 流
  - `delta`: 追加增量
  - `end`: 该流结束，不再发送更多 chunk
  - `snapshot`: 一次性完整内容，常用于 cache hit 或非真流式 fallback
- `replace_stream`
  - 布尔值
  - 仅允许在 `content_phase=start` 或 `snapshot` 时出现
  - 含义是“先清空同 `content_stream_id` 的旧缓冲，再写入当前内容”

补充规则：

- `preview` 事件必须带 `content_stream_id`
- `preview` 事件必须带 `content_phase`
- `final` 事件可以不带 `content_stream_id`；若带，固定使用 `final:answer`
- `final` 事件若采用多条流式输出，必须带 `content_phase`
- `final` 事件若省略 `content_phase`，等价于单条 `snapshot`
- 同一个 `content_stream_id` 只能有一个打开中的流
- 收到 `content_phase=end` 后，该 `content_stream_id` 不可再继续发送 `delta`
- `preview` 若省略 `content_phase` 视为非法协议，不允许发送

兼容规则：

- 不能依赖“老前端忽略新字段”实现兼容
- 服务端必须引入 capability gate，例如请求头、query flag 或内部路由开关
- 只有 capability enabled 时，后端才允许发送 `preview` 事件
- capability disabled 时，后端只能发送传统单通道 `final` 语义内容
- 新前端仅在 `patent` 文件问答链路启用增强消费

## Ordering Contract

以下是强制顺序规则：

1. `step` 与 `content` 可以交错，但 `preview` 和 `final` 必须满足以下约束。
2. 对同一 `content_stream_id`：
   - 第一条事件只能是 `start` 或 `snapshot`
   - `delta` 只能出现在 `start` 之后、`end` 之前
   - `end` 最多出现一次
3. 对 hybrid 路径：
   - `preview` 可以跨来源顺序出现，但默认不要求并发交错
   - 每个已经打开的 `preview` 流，必须在第一条 `final` 事件发出前满足以下二选一之一：
     - 已显式发送 `end`
     - 最后一条事件本身就是 `snapshot`
   - 一旦第一条 `final` 事件发出，不允许再出现任何新的 `preview` 事件
   - 若实现侧无法逐个补发 `end`，则必须在开始 `final` 前先发送对应 preview 流的终止事件
   - `final` 可以是 `start + delta* + end`，也可以是单条 `snapshot`
4. 对 cache hit：
   - 不发送 `preview`
   - 只发送 `final` 的 `snapshot`
5. 对 capability disabled：
   - 不发送 `preview`
   - 所有内容事件都按传统正文处理

## Backend Design

### 1. 保留现有 `content_callback`，新增 role/source 包装层

不要在所有 service 内部把 callback 改成复杂对象。

推荐做法：

- 在 `executor` 或 `file_routes` 一层提供轻量 wrapper
- wrapper 负责把纯文本 chunk 包装成带 `content_role` / `content_source` 的事件
- wrapper 只对 `patent` 文件/混合问答链路生效
- standalone `kb_qa` 不接入该 wrapper 扩展

### 2. 拆分“来源预览流”和“最终答案流”

`pdf_service`:

- `pdf_qa` 只发 `final/pdf`
- `hybrid_qa` 且 capability enabled 时，允许发 `preview/pdf`
- 最终 `answer_text` 仍作为分支结果返回

`tabular_service`:

- `tabular_qa` 只发 `final/table`
- `hybrid_qa` 且 capability enabled 时，若需要来源级显示，可发 `preview/table`
- 若底层不支持真流式，可使用单条 `snapshot` 形式发送 `preview/table`

`kb_service`:

- standalone `patent kb_qa` 完全不改
- `hybrid_qa` 文件链路中，短期可先不实现 `preview/kb`
- 即便短期不做 `preview/kb`，也不能继续阻断文件侧 `preview`

### 3. `hybrid` 最终合成单独走 `final/hybrid`

最终统一答案的唯一正文通道是：

- `content_role=final`
- `content_source=hybrid`

这样可以避免 UI 把来源预览和最终正文混为一体。

### 4. 取消当前两处“硬断流”

需要改掉两类现状：

- executor 中 `includes_kb` 时把 file route callback 置空
- file-only hybrid 中对子分支统一传 `None`

但不是简单改成“都透传原 callback”，而是改成“透传 preview wrapper”。

### 5. capability gate 是服务端强约束

服务端在以下任一条件未满足时，必须禁用 `preview`：

- 前端未声明支持增强流式协议
- 请求不是 `patent` 文件/混合问答链路
- 路由为 standalone `kb_qa`

禁用后行为：

- 仍允许 step 流
- 只允许最终答案内容流
- 不允许任何 `preview/*` 事件泄漏到旧前端

## Frontend Design

前端只做 `patent` 文件问答链路适配。

目标状态：

- `final` 内容进入主回答正文
- `preview` 内容进入来源级流式预览区
- 若前端尚未拿到 `final`，可展示“正在汇总最终答案”

最小可行 UI：

- 一个主回答区
- 一个可折叠“处理中证据”区
- `pdf` / `table` / `kb` 各自独立缓冲

前端消费规则：

- `final/*` 只写入主回答区
- `preview/*` 只写入对应 `content_stream_id` 的预览缓冲
- `replace_stream=true` 时先清空同 id 缓冲再写入
- 收到 `content_phase=end` 后标记该预览流完成
- capability disabled 时，维持当前单回答消费逻辑

## Observability

必须保留并整理这类日志：

- route / source_scope / handler
- content callback 是否透传
- preview/final chunk 数
- 首个 step 时间
- 首个 preview 内容时间
- 首个 final 内容时间

需要能回答：

- 是没流，还是流到了 preview 区
- 是被 callback 清空了，还是模型本身没吐 chunk
- 是缓存回放，还是实时生成

## Risks

1. 若 capability gate 漏配，老前端会把 `preview` 当正文显示。
2. hybrid 最终答案和 preview 可能内容重复，导致 UI 观感冗余。
3. KB 分支时延长，可能让 preview 和 final 间隔非常长。
4. 若 `content_stream_id` / `content_phase` 处理不好，前端来源卡片可能乱序或无法闭合。

## Rollout Strategy

Phase 1:

- 加协议字段
- 加 capability gate
- 前端兼容消费
- 不改现有流式路径，只支持识别新字段和 gate

Phase 2:

- 保持 `pdf_qa` 为 `final/pdf`
- 用新协议字段承载现有 `pdf_qa` 正文流
- 验证 capability enabled/disabled 两种前端都不回归

Phase 3:

- 打开 `hybrid_qa pdf+table` 的 `preview/pdf` 和 `preview/table`
- 最终答案改走 `final/hybrid`

Phase 4:

- 打开 `+kb` 路径的文件侧 preview 流
- 视成本决定是否增加 `preview/kb`

## Acceptance Criteria

1. capability disabled 时，`hybrid_qa` 的 SSE 流中不得出现任何 `content_role=preview` 事件；旧前端主回答渲染结果与当前单通道行为一致。
2. capability enabled 时，`pdf_qa` 的 SSE 内容事件只允许出现 `content_role=final`、`content_source=pdf`。
3. capability enabled 时，`tabular_qa` 的 SSE 内容事件只允许出现 `content_role=final`、`content_source=table`。
4. capability enabled 时，`hybrid_qa pdf+table` 在第一条 `final` 事件之前，必须至少出现一条 `preview/pdf` 或 `preview/table` 事件；第一条 `final` 事件之后不得再出现 `preview`。
5. capability enabled 时，`hybrid_qa pdf+kb` 的文件侧必须允许 `preview/pdf`；standalone `patent kb_qa` 的 SSE 契约不得新增 `preview`。
6. cache hit 的文件/混合问答请求只允许发送 `final` `snapshot`，不得回放 `preview`。
7. 新前端主回答区只能展示 `final` 内容；`preview` 内容只能出现在预览区，且按 `content_stream_id` 分桶。
8. 非 `patent` 模式流式协议和渲染行为不受影响。

## Non-Goals

本 spec 不承诺：

- 立即解决文件总结内容太短的问题
- 解决模型首 token 慢的问题
- 统一所有模式的流式协议
- 一次性重构 `patent` 整个执行架构
