# 文件 QA 与混合 QA 审查总结

## 1. 文档目的

这份文档是对“文件 QA / 混合 QA”当前审查结果的集中总结。

它回答四个问题：

1. 这次 review 的范围是什么
2. 目标架构是什么
3. 当前系统对齐到什么程度
4. 还缺什么，应该按什么顺序修

这份文档是总览入口。
更细的协议、覆盖范围、任务拆解、细粒度对齐审查，分别见：

- [file_hybrid_qa_coverage_zh.md](/home/cqy/worktrees/highThinking/docs/file_hybrid_qa_coverage_zh.md)
- [file_hybrid_qa_protocol_spec.md](/home/cqy/worktrees/highThinking/docs/file_hybrid_qa_protocol_spec.md)
- [file_hybrid_qa_task_breakdown.md](/home/cqy/worktrees/highThinking/docs/file_hybrid_qa_task_breakdown.md)
- [fastQA_file_mode_alignment_review.md](/home/cqy/worktrees/highThinking/docs/fastQA_file_mode_alignment_review.md)

## 2. 审查范围

本次 review 只看文件参与的问答链路。

包括：
- `pdf_qa`
- `tabular_qa`
- `hybrid_qa`
- gateway 文件路由与上下文分发
- fastQA 文件执行链
- 文件相关流式事件与前端兼容面

不包括：
- 普通 `kb_qa`
- `highThinkingQA` 的独立问答链路
- `patent` 链路
- 公共服务的登录、鉴权、会话管理本身

## 3. 目标架构

当前设计目标已经明确，不再沿用旧版单体里“同一个后端自己判断意图、自己选文件、自己执行”的模式。

目标边界是：

### 3.1 gateway 负责

- 拉取会话绑定文件
- 判断这轮是否属于文件问答
- 判断是 `pdf_qa`、`tabular_qa` 还是 `hybrid_qa`
- 决定 `source_scope`
- 决定 `turn_mode`
- 决定本轮到底哪些文件进入执行
- 把规范化后的 `used_files / execution_files / route / source_scope` 发给 fastQA

### 3.2 fastQA 负责

- 校验 gateway 下发的执行上下文是否合法
- 加载文件
- 执行 PDF / 表格 / 混合问答链路
- 在需要时做 KB 补充
- 输出稳定的流式事件
- 返回引用、来源、答案

### 3.3 fastQA 不再负责

- 从原始问题重新判断意图
- 自己重新选文件
- 自己把 `conversation_id` 解释成文件选择结果
- 静默改路由

一句话：

> gateway 是文件意图和路由的唯一权威，fastQA 是执行器。

## 4. 基线来源

这次审查同时对照两个基线：

### 4.1 旧版执行基线

旧版来源：`/home/cqy/worktrees/fastapi-version/backend`

它用于回答：
- 旧版 `pdf_qa / tabular_qa / hybrid_qa` 在模块层面是怎么工作的
- 旧版 ask-gateway 的活跃执行路径是什么

### 4.2 新版协议基线

新版目标来源：
- [file_hybrid_qa_protocol_spec.md](/home/cqy/worktrees/highThinking/docs/file_hybrid_qa_protocol_spec.md)
- [file_hybrid_qa_coverage_zh.md](/home/cqy/worktrees/highThinking/docs/file_hybrid_qa_coverage_zh.md)

它用于回答：
- 新系统里应该由谁判断意图
- 混合 QA 应该覆盖哪些 source scope
- 哪些差异是“架构升级”，哪些才是真缺陷

## 5. 当前总体结论

## 5.1 不是空壳

当前文件问答和混合问答不是空壳。
主链路已经存在：

- 前端把文件上下文带到 gateway
- gateway 解析会话文件并做路由判断
- gateway 把规范化请求转发给 fastQA
- fastQA 已经有 `pdf_qa / tabular_qa / hybrid_qa` 的执行模块

这一点已经不是“还没开始做”。

## 5.2 但没有完全收口

当前问题不在于“有没有”，而在于“边界和执行语义还没有完全对齐”。

换句话说：

- 协议层已经基本想清楚了
- 覆盖范围也已经定义清楚了
- 但 live 路径和旧版对齐、以及 gateway/fastQA 的边界清理，还没有彻底完成

## 6. 当前对齐度判断

以下是基于代码路径审查的工程判断，不是压测结论：

| 项目 | 当前状态 | 判断 |
| --- | --- | --- |
| 文件 QA 范围定义 | 已明确 | 基本完成 |
| `pdf_qa` 模块级逻辑 | 大体保留 | 中高对齐 |
| `tabular_qa` 模块级逻辑 | 最接近旧版 | 高对齐 |
| `hybrid_qa` 服务逻辑 | 已有主链路 | 中等偏上 |
| gateway 文件路由边界 | 已建立 | 方向正确，但还需继续收口 |
| fastQA 作为纯执行器 | 部分做到 | 仍有重复解释和本地路径依赖 |
| 文件执行分布式闭环 | 未完成 | 主要阻塞项 |
| 流式协议与前端兼容 | 已有实现 | 还需要继续锁死协议 |
| 回归测试覆盖 | 不够厚 | 明显不足 |

## 7. 这次 review 的关键发现

### 7.1 范围定义已经清楚

以下分类已经明确：

- 只用 PDF -> `pdf_qa`
- 只用表格 -> `tabular_qa`
- `pdf+kb` -> `hybrid_qa`
- `table+kb` -> `hybrid_qa`
- `pdf+table` -> `hybrid_qa`
- `pdf+table+kb` -> `hybrid_qa`

这意味着：

- “PDF + 普通问题”本质上不是 `pdf_qa`，而是 `hybrid_qa`
- “表格 + 普通问题”也是 `hybrid_qa`
- 多 PDF 不等于混合 QA
- 多表格也不等于混合 QA

这部分范围已经可以作为验收基线。

参考：
- [file_hybrid_qa_coverage_zh.md](/home/cqy/worktrees/highThinking/docs/file_hybrid_qa_coverage_zh.md)

### 7.2 gateway 边界方向是对的

新的系统设计里，文件意图判断应该归 gateway。
这件事现在已经不是纯概念，代码里已经有主链路：

- gateway 拉会话文件
- gateway 做 route decision
- gateway 生成上游 payload
- fastQA 接收规范化上下文执行

因此，不能再要求 fastQA 按旧版单体方式自己做顶层文件意图判断。

这类差异应算“架构迁移”，不能直接算执行 bug。

### 7.3 fastQA 的模块代码不等于 live 路径完全对齐

这是这次 review 的核心结论之一。

`pdf_qa`、`tabular_qa`、`hybrid_qa` 的模块文件很多都已经迁过来了，但活跃 HTTP 路径是否等价，不只取决于模块本身，还取决于：

- router
- request adapter
- file route wrapper
- stream contract
- gateway 下发字段

所以不能只看 `qa_pdf/` 或 `qa_tabular/` 文件本身，就认定 live 行为已经对齐旧版。

### 7.4 `tabular_qa` 当前最接近旧版

从现有审查结果看：

- `tabular_qa` 的 planner / executor / renderer / loader 整体最接近旧版
- 它的核心服务逻辑比 `pdf_qa` 和 `hybrid_qa` 更稳定

但问题仍然在执行载体上：

- 当前 live 路径仍然依赖 `local_path`
- 对分布式执行不友好

### 7.5 `pdf_qa` 模块近似，但 live 语义仍不完全一致

`pdf_qa` 的抽取、截断、prompting、engine、service、streaming 等模块，大体保留了旧版结构。

但 live 行为仍有差异，典型包括：

- KB verification 的接线方式不完全一致
- route wrapper 已不是旧版 ask-gateway 那一套
- web binding / llm factory 等外围简化过
- 当前系统的协议和事件归一化已经变化

所以它不是“没做”，而是“模块对齐明显高于 live 语义对齐”。

### 7.6 `hybrid_qa` 已存在，但 active 路由还不能说完全等价

`hybrid_qa` 的核心逻辑已经不算空白。
但当前 active route 的行为和旧版不完全一样，尤其是：

- 混合模式的 source_scope 还没有完全冻结到全链路
- `pdf+kb`、`table+kb`、`pdf+table`、`pdf+table+kb` 四个子场景还需要继续锁定执行语义
- 某些旧版路径更偏向 preview fallback，而新版已经开始接 chunk / richer execution path

这不是单点 bug，而是“协议已设计、实现还在收口”的状态。

### 7.7 当前最大工程缺口不是协议，而是执行闭环

这次 review 最需要继续盯住的不是“混合 QA 应不应该支持某个场景”，而是下面两个执行问题：

1. fastQA 仍依赖 `local_path` 执行文件问答
2. 回归测试不足，不能证明 live 路径已经和目标协议稳定一致

这两个问题决定了系统现在是否能稳态运行，而不是文档是否写清楚。

## 8. 当前最重要的缺口

### High

1. fastQA 对上传文件执行仍依赖 `local_path`
- 这会导致跨实例执行、节点漂移、重启恢复时闭环不稳。
- 这是文件 QA / 混合 QA 真正落地的核心阻塞项。

2. fastQA 还没有完全收成“纯执行器”
- 虽然方向已经对，但 live 路径里仍有一些二次解释、二次兜底或语义漂移风险。

3. 文件问答的回归测试明显不够厚
- 目前不能只凭代码阅读就宣称和旧版 fully aligned。

### Medium

1. `pdf_qa` live 行为与旧版 ask-gateway 包装层仍有差异
2. `hybrid_qa` 四种 source_scope 子场景还需要逐个锁死
3. 流式事件契约虽然已有实现，但还需要继续冻结 vocabulary 和前端兼容面

### Low

1. 文档层面已经比较完整
2. 范围定义已经比较清楚
3. 现在更像是工程对齐问题，不是需求定义问题

## 9. 后续修复顺序

按收益和风险排序，建议继续按这个顺序推进：

1. 冻结 gateway -> fastQA 文件协议
- `route`
- `source_scope`
- `turn_mode`
- `used_files`
- `execution_files`
- `primary_file_id`

2. 让 fastQA 彻底变成纯执行器
- 只校验，不重判意图
- 不静默改 route
- 不重新选文件

3. 先补执行闭环
- 上传文件执行不要再只依赖 `local_path`
- 要能从统一文件来源完成物化/执行

4. 逐路由对齐
- `pdf_qa`
- `tabular_qa`
- `hybrid_qa`

5. 锁定流式协议和前端兼容
- 事件类型
- step 元数据
- done 语义
- citation/source metadata

6. 最后补厚测试矩阵
- route 覆盖
- source_scope 覆盖
- 文件缺失/错误类型覆盖
- 前端兼容覆盖

## 10. 最终一句话结论

如果问“文件 QA / 混合 QA 现在有没有成型”，答案是：

> 已经成型，不是空壳，范围和目标边界也已经讲清楚了。

如果问“现在是不是已经完全闭环、可以当作和旧版等价的稳定实现”，答案是：

> 还不能这么说。当前最大缺口不是协议定义，而是 fastQA 的执行闭环、live 语义收口，以及测试覆盖不足。

## 11. 关于‘记忆’

当前会话里的这些结论我还能接上。
但跨会话不应该依赖模型记忆，所以这份文档和相关细化文档就是后续工作的稳定基线。
