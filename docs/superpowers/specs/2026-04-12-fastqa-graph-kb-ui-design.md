# FastQA Graph KB Result UI Design

**Date:** 2026-04-12

## Summary

本设计定义如何在现有 `fastQA` 文献知识图谱能力之上，低侵入地优化图谱命中结果的展示质量。

目标不是新增一套独立图谱前端协议，而是：

- 保持现有 `fastQA` 聊天气泡、SSE、markdown 渲染和 PDF 阅读器主链不变
- 只对 `graph_kb` 命中结果输出更稳定、更结构化的内容
- 在前端仅增加 `graph_kb` 结果的局部样式增强
- 不影响当前 `kb_qa` 向量主链、文件问答链路和 `hybrid_qa` 既有语义

---

## Scope

本设计只覆盖：

- `fastQA` 中 `query_mode=graph_kb` 的回答展示
- 文献图谱命中后的文献列表展示
- 文献 DOI 展开类图谱问题的详情展示
- 图谱结果中的脏数据清洗和结构化 markdown 组织
- 前端基于现有 markdown 渲染链路的 graph-only 样式增强
- 图谱命中时的处理步骤展示约定

本设计明确不覆盖：

- 专利图谱
- 图谱与向量混合排序或双路融合
- 新增独立图谱后端或前端显式开关
- 重写现有聊天消息协议
- 新增图谱专属 Vue 组件协议
- 修改 `pdf_qa / tabular_qa / hybrid_qa`
- 修改非图谱回答的 markdown 呈现
- 第一阶段的分页、虚拟滚动或复杂交互式详情面板

---

## Goals

1. 解决图谱文献列表“一整段文本堆叠”的可读性问题
2. 清理 `_null_`、`null_`、残缺 DOI、重复命中描述等图谱脏输出
3. 将 DOI 展开类结果整理为稳定分段的详情结构
4. 让图谱命中结果在现有聊天 UI 中更容易识别，但不引入新的重协议
5. 保持图谱失败时静默回退到当前 `kb_qa` 向量主链
6. 不影响文件侧 `hybrid_qa`、PDF 阅读器、参考文献链接链路

---

## Non-Goals

本设计不包含：

1. 将图谱结果改造成新的结构化 JSON 协议并由前端专用组件渲染
2. 在第一阶段实现“文献多级折叠卡片 + 分页 + 前端筛选”
3. 在第一阶段承诺“命中置信度”统一展示
4. 在第一阶段做图谱结果和向量结果混合回答
5. 在第一阶段为所有问答模式统一重做 markdown 视觉风格

---

## Background

### 1. 当前前端渲染链路

当前前端消息主链已经具备以下能力：

- SSE `done` 事件可写入 `final_answer / references / reference_links / query_mode / steps`
- 聊天气泡正文通过 [`frontend-vue/src/utils/index.js`](/home/cqy/worktrees/highThinking/frontend-vue/src/utils/index.js) 中的 `formatAnswer()` 走 markdown 渲染
- `query_mode` 会在 [`frontend-vue/src/views/Home.vue`](/home/cqy/worktrees/highThinking/frontend-vue/src/views/Home.vue) 中渲染为 badge
- `reference_links / pdf_links` 已和现有 PDF 阅读器 [`frontend-vue/src/components/PdfReader.vue`](/home/cqy/worktrees/highThinking/frontend-vue/src/components/PdfReader.vue) 打通

因此，图谱 UI 优化不需要新建结果协议，只需要让图谱答案本身更结构化，并在前端识别图谱消息时补样式。

### 2. 当前图谱答案问题

当前 `graph_kb` 成功命中时，后端 [`fastQA/app/modules/graph_kb/service.py`](/home/cqy/worktrees/highThinking/fastQA/app/modules/graph_kb/service.py) 主要通过确定性字符串拼接返回回答。

这种方式有几个直接问题：

- 文献列表以长段落形式输出，缺少条目边界
- 每篇文献后重复追加“原料命中”文字，冗余严重
- DOI 展开详情中可能包含 `_null_`、`null_`、脏分隔符
- 类似 `method_ball milling_time_null_speed_null...` 的字段未经结构化整理
- 前端虽然支持 markdown，但当前图谱答案没有充分利用

### 3. 现有步骤展示链路

当前前端会渲染后端发回的 `steps`。因此图谱命中后的流程不需要新增前端机制，只需要在后端图谱命中链路中继续按既有步骤协议发阶段信息。

### 4. 设计选择

本次已确认采用视觉方向 `A`：

- 后端输出结构化 markdown
- 前端仅对 `graph_kb` 结果增加局部样式增强

不采用：

- 图谱专属复杂卡片组件
- 双栏详情布局
- 图谱专属新协议

---

## Requirements

### Functional Requirements

1. 图谱命中时，文献列表必须按独立条目展示，而不是单段堆叠
2. 图谱 DOI 展开类问题必须输出分段详情
3. `_null_`、`null_`、空白占位值、重复原料命中描述必须被清洗
4. 图谱回答必须至少保留正文 DOI 点击打开 PDF 的能力；若要与标准 `kb_qa` `done` 对齐，则应补齐现有可选字段 `reference_links / pdf_links / doi_locations`
5. 图谱步骤展示必须兼容当前“处理过程”面板

### Safety Requirements

1. 只允许影响 `query_mode=graph_kb` 的结果格式
2. `kb_qa` 向量主链、`pdf_qa`、`tabular_qa`、`hybrid_qa` 不得受影响
3. 图谱渲染失败、图谱数据清洗后为空时，必须继续静默回退主链
4. 前端 graph-only 样式不得污染普通 markdown 回答

### UX Requirements

1. 图谱消息一眼可识别，但仍保持当前聊天产品视觉体系
2. 列表标题、DOI、命中条件、详情章节应具备清晰层级
3. 文献详情必须至少区分：
   - 基础信息
   - 测试/表征
   - 制备/工艺
   - 关键参数
4. 长列表第一阶段需要有限制，避免单条消息过长

---

## Alternatives Considered

### Option A1: 纯后端结构化 Markdown

做法：

- 后端生成结构化 markdown
- 前端完全不改

优点：

- 侵入最小
- 回滚最简单

缺点：

- 视觉提升有限
- 图谱消息与普通 markdown 消息区分度不够

结论：

- 不采用，作为兜底思路保留

### Option A2: 后端结构化 Markdown + 前端 Graph-Only 样式增强

做法：

- 后端输出稳定 markdown
- 前端只在图谱消息气泡上附加局部 class 并增强排版

优点：

- 仍沿用当前协议
- 风险低
- 能显著改善列表和详情可读性

缺点：

- 不如专属组件那样高度结构化

结论：

- 采用

### Option B: 新增图谱专属结构化结果协议

做法：

- 后端返回 graph result JSON
- 前端专门写图谱组件渲染

优点：

- 最灵活
- 后续可扩展性更高

缺点：

- 协议侵入大
- 容易碰现有 markdown、steps、referenceLinks 链路
- 不符合当前“低侵入优先”的要求

结论：

- 不采用

---

## Design Summary

本设计采用“两层收敛”方案：

1. 后端层：只对 `graph_kb` 输出做结构化整理和清洗
2. 前端层：只对图谱消息 markdown 做局部样式增强

运行时原则：

- 图谱识别、图谱查询、图谱回退逻辑维持现状
- 成功命中图谱后，不再拼长段落文本，而是输出稳定 markdown
- markdown 中包含文献概览、文献列表、文献详情等分段
- 图谱正文中的 DOI 继续复用现有点击打开 PDF 能力；若补齐标准 `done` 字段，则图谱消息也可获得与 `kb_qa` 相同的 DOI 元数据对齐能力
- 非图谱消息继续沿用当前渲染和样式，不被波及

---

## Backend Design

### 1. 输出协议保持不变

后端继续返回现有核心字段：

- `answer`
- `references`
- `query_mode`
- `steps`

不新增图谱专属顶层协议字段。

如果图谱结果需要与当前 `kb_qa` 的 DOI/PDF 元数据体验对齐，则图谱 `done` 事件应补齐当前主链已使用的可选字段：

- `reference_links`
- `pdf_links`
- `doi_locations`

这些字段已经存在于普通 `kb_qa` `done` 协议中，因此这属于“对齐既有协议”，不是新增图谱专属协议。

### 2. 图谱回答改为结构化 markdown

`graph_kb` 的 `answer` 不再是单段叙述，而是按模板渲染 markdown。

第一阶段至少覆盖两类模板：

#### `list_by_raw_material`

示例结构：

```md
## 📚 文献概览
- 当前展示 10 篇相关文献
- 原料：LiFePO4
- 查询类型：按原料查文献

## 📖 相关文献
### [1] Scalable synthesis of N-doped Si/G@voids@C with porous structures for high-performance anode of lithium-ion batteries
- DOI：10.xxxx/xxxx
- 命中条件：原料 = LiFePO4

### [2] In-situ reconstruction of N-doped carbon nanoflower coating layer for enhancing high pseudo-capacitance in Bi-based fast-charging lithium-ion batteries
- DOI：10.xxxx/xxxx
- 命中条件：原料 = LiFePO4
```

规则：

- 标题优先展示 `title`，缺失时回退 DOI
- DOI 独立一行展示
- 命中条件只在每篇条目中展示一次
- 第一阶段限制展示前 `N` 篇，建议 `N=10`
- 由于当前图谱执行层会先按 `max_rows` 截断返回结果，第一阶段只承诺展示“当前返回/展示的条数”，不承诺真实总量
- 若返回结果已命中上限，末尾可追加“结果已按上限截断”

#### `expand_doi_context_by_doi`

示例结构：

```md
## 📄 文献信息
- 标题：Scalable synthesis ...
- DOI：10.xxxx/xxxx

## 🔬 测试/表征
- XRD
- EIS

## ⚙️ 制备/工艺
### Ball milling
- 时间：12 h
- 转速：350 rpm

## 📌 关键参数
- 干燥温度：110 °C
- 膜厚度：30 μm
```

规则：

- 文献基础信息单独成段
- 测试/表征、制备/工艺、关键参数分段
- 若某段无有效内容，则整段省略
- 避免输出空标题和空列表

### 3. 图谱数据清洗层

需要在 `fastQA/app/modules/graph_kb/service.py` 内，为 markdown 渲染补一层 graph-only 清洗工具。职责包括：

- 清理 `_null_`、`null_`、孤立下划线、重复分隔符
- 清理空字符串、`None`、无意义占位值
- 保留已修复的 graph DOI 轻量清洗，不复用主链过重的 DOI 规范化逻辑
- 将带有键前缀的脏字段拆成“字段名 + 值”的更稳定结构

### 4. 详情字段解析策略

图谱详情常见脏格式类似：

- `method_ball milling_time_null_speed_null`
- `vacuum drying_temperature_110C_time_12h`

第一阶段不尝试做“通用自然语言理解”，只做规则型解析：

1. 先做 token 清洗，去掉 `_null_`、空 token
2. 识别常见键前缀：
   - `method`
   - `time`
   - `temperature`
   - `speed`
   - `ball_powder_ratio`
   - `atmosphere`
   - `thickness`
3. 将结果归并为：
   - 方法标题
   - 方法下的参数列表
4. 无法稳定解析的残余内容，作为普通列表项输出，不因格式问题丢失整条信息

该策略要求“宁可保守，也不误伤”：

- 不强行把未知 token 解释成错误字段
- 不因单个字段异常导致整段详情不可见

### 5. 模板职责边界

图谱模板渲染层应保证：

- 一个模板只负责一种问题语义
- 每个模板输出稳定 markdown 结构
- 模板内部不拼接冗长自然语言段落
- 模板输出先经过清洗，再进入 markdown 渲染

---

## Frontend Design

### 1. 不引入新组件协议

前端继续使用：

- [`frontend-vue/src/views/Home.vue`](/home/cqy/worktrees/highThinking/frontend-vue/src/views/Home.vue)
- [`frontend-vue/src/utils/index.js`](/home/cqy/worktrees/highThinking/frontend-vue/src/utils/index.js)

中的既有 markdown 渲染主链。

不新增图谱专属数据协议，不改 `referenceLinks`、`references`、`steps` 的处理方式。

### 2. 图谱消息识别

前端基于已存在的 `queryMode` / `query_mode` 识别图谱消息。

建议在 assistant 消息容器或消息正文节点上增加 graph-only class，例如：

- `message-graph-kb`

触发条件：

- `entry.message.queryMode === '知识图谱'`
- 或底层 `metadata.query_mode === 'graph_kb' / 'neo4j'`

### 3. Graph-Only 样式增强

`Home.vue` 当前使用 `scoped` 样式，而图谱正文来自 `v-html`。因此所有图谱 markdown 样式增强都必须通过 `:deep(...)` 命中渲染后的 HTML 节点，不能只写普通 scoped 选择器。

只在 `message-graph-kb` 作用域内增强 markdown 样式：

- `h2`：主章节标题，强化为图谱段落标题
- `h3`：文献条目标题或方法小节标题
- `ul / li`：增大间距和缩进，增强可扫读性
- DOI 链接：强调颜色和 hover 状态
- 不同文献条目之间增加轻分隔
- 章节间距拉开，避免长段堆叠

视觉原则：

- 保持现有白底气泡体系
- 使用浅蓝系章节强调，避免引入全新视觉语言
- 不破坏移动端宽度和折行

### 4. 不改普通消息 markdown

必须避免以下风险：

- 图谱标题样式污染普通回答
- 普通回答中的 `h2 / h3 / ul` 被意外重排
- PDF、专利、文件混合问答的正文样式发生变化

因此，所有新增样式都必须挂在 graph-only class 下。

---

## Step Presentation

图谱命中时，后端继续使用当前步骤协议。

建议图谱命中的步骤最少包含：

1. 识别问题是否适合图谱
2. 查询文献图谱
3. 整理图谱结果

要求：

- 步骤面板继续由当前前端组件渲染
- 不新加图谱步骤前端协议
- 图谱失败或未命中时，步骤应能自然衔接后续向量主链过程

---

## References And PDF Flow

图谱 UI 优化不得改动 DOI -> PDF 的既有链路。

当前代码下，正文 DOI 点击已经是现成能力；而 graph `done` 事件尚未像普通 `kb_qa` 一样补齐 `reference_links / pdf_links / doi_locations`。

因此本设计将 PDF 交互分成两层：

1. 第一层，正文 DOI 点击：
   - 图谱 markdown 中出现 DOI 时，前端继续通过现有 `.doi-link` 点击逻辑打开 `PdfReader`
2. 第二层，`done` 字段对齐：
   - 若图谱路径补齐现有 `reference_links / pdf_links / doi_locations`，则可与普通 `kb_qa` 的 DOI 元数据体验保持一致

这意味着：

- 第一阶段不需要新增图谱专属 PDF API
- 第一阶段也不需要假设存在独立“引用区”UI
- 文献标题不必在第一阶段做新的点击协议
- 正文 DOI 是最基础、最稳的 PDF 入口

---

## Error Handling And Fallback

### 1. 图谱回退边界

下列情况必须继续静默回退到主链：

- 图谱分类不命中
- 图谱模板规划失败
- 图谱查询超时
- 图谱结果为空
- 图谱结果清洗后为空
- 图谱模板渲染后为空

### 2. 图谱 UI 安全边界

下列情况不得影响主链：

- 某个详情字段无法解析
- 某篇文献缺少标题
- 某条 DOI 被图谱专用清洗过滤
- 某个章节无有效内容

处理原则：

- 局部跳过
- 整体尽量保留
- 只有在整份图谱结果不可用时才回退主链

---

## Testing Strategy

### Backend Tests

需要覆盖：

1. `list_by_raw_material` 输出结构化 markdown
2. `expand_doi_context_by_doi` 输出分段 markdown
3. `_null_ / null_` 清洗生效
4. 重复命中描述不再冗余堆叠
5. 脏详情字段规则型解析稳定
6. 图谱结果清洗后为空时继续 `render_empty -> fallback`
7. 已修复 DOI 边界用例不回归

### Frontend Tests

至少覆盖：

1. `graph_kb` 消息挂载 graph-only class
2. 图谱消息 markdown 标题、列表、章节样式通过 `:deep(...)` 生效
3. 普通消息不受 graph-only 样式影响
4. 图谱消息仍能展示 `steps`
5. 图谱正文 DOI 点击仍可打开 PDF 阅读器
6. 若图谱 `done` 事件补齐 `reference_links / pdf_links / doi_locations`，则对应元数据可被前端正常消费

### Manual Verification

至少验证以下问题：

1. `有哪些使用 LiFePO4 作为原料的文献？`
2. `10.xxxx/xxxx 这篇文献包含哪些测试/表征和工艺信息？`
3. 一个明显不适合图谱的问题，确认仍走向量主链
4. 图谱命中时前端“处理过程”显示正常
5. 图谱正文 markdown 显示正常，正文 DOI 可继续打开 PDF

---

## Rollout Plan

建议按以下顺序落地：

1. 后端图谱 markdown 模板重构
2. 后端图谱详情清洗和字段解析
3. 前端 graph-only class 与局部样式
4. 图谱命中示例联调
5. 回归测试，确保非图谱链路不受影响

---

## Open Questions

本设计暂时保留但不阻塞落地的问题：

1. 第一阶段是否只以正文 DOI 点击作为 PDF 入口，还是同步补齐 graph `done` 的 `reference_links / pdf_links / doi_locations` 以获得与 `kb_qa` 一致的 DOI 元数据体验
2. 长文献列表后续是否需要“展开更多”而不只是截断
3. 图谱详情字段是否需要进一步做中英文字段名规范化
4. 未来若接入专利图谱，是否沿用同一套 markdown+样式策略

---

## Acceptance Criteria

满足以下条件即可认为本设计交付达标：

1. 图谱文献列表不再以长段落形式输出
2. 图谱详情不再暴露 `_null_`、`null_` 等脏字符串
3. 图谱详情至少按基础信息、测试/表征、制备/工艺、关键参数分段
4. 前端仅在图谱消息中增强 markdown 视觉效果
5. 现有 `kb_qa` 向量主链和文件问答链路无回归
6. 现有引用与 PDF 阅读器链路可继续使用
