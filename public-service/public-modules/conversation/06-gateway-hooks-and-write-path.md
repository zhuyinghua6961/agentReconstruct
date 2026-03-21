# conversation 写路径与 gateway 持久化钩子

对应代码：
- `backend/app/modules/conversation/service.py`

## 1. 主写路径有哪些

对 `conversation` 真正改数据的公开入口主要有：

- `create_conversation()`
- `update_conversation_title()`
- `add_message()`
- `add_uploaded_file()`
- `remove_uploaded_file()`
- `update_uploaded_file_processing_state()`
- `delete_conversation()`

此外还有两个“运行态写入口”：
- `persist_user_request()`
- `persist_assistant_summary()`

这两个不是 API 路由直接暴露给前端的，而是给 ask_stream/gateway 调用的。

## 2. 创建会话的写链路

顺序是：

1. 插入 `conversations`
2. 构造默认 JSON 文档
3. `_persist_document_and_index()`
4. 刷新 detail cache
5. 刷新 list cache

这里意味着：
- 创建会话不仅要有 DB 行，还必须立即有 JSON 主文档

## 3. 改标题的写链路

`update_conversation_title()` 的行为是：

1. 校验会话存在
2. 更新 `conversations.title`
3. 进入单会话锁
4. 读取或回填 JSON 文档
5. 更新 `meta.title`、`meta.updated_at`
6. `_persist_document_and_index()`
7. 刷新 list/detail cache

说明：
- 标题不是只改数据库
- JSON 文档里的 `meta.title` 也保持同步

## 4. 新增消息的写链路

`add_message()` 是当前消息持久化主路径。

流程：

1. 校验 `role` 只能是 `user` / `assistant`
2. 校验内容非空
3. 校验会话存在
4. 在单会话锁内读取/回填文档
5. 计算下一个 `message_id`
6. 追加消息到 JSON `messages`
7. 更新 `meta.updated_at / message_count / last_message_at`
8. `_persist_document_and_index()`
9. 回写 `conversations.message_count`
10. 刷新 list/detail cache

关键事实：
- 这里不再调用 `ConversationRepository.add_message()`
- `conversation_messages` 旧表没有参与主写入

## 5. 新增文件的写链路

`add_uploaded_file()` 与消息不同，它是双写起步：

1. 校验 `file_type`
2. 校验会话存在
3. 先插入 `conversation_files`
4. 在单会话锁内读取/回填 JSON
5. 把新文件项追加到 JSON `files`
6. `_persist_document_and_index()`
7. 刷新 list/detail cache

这里的风险很明确：
- 如果第 3 步成功，第 6 步失败，会留下旧表记录但 JSON 未完整同步
- 当前代码靠后续读路径回填和状态修复来兜住

## 6. 删除文件的写链路

`remove_uploaded_file()` 分两段写：

第一段：
- 在 JSON 中把文件标记为 `deleted`
- 写入：
  - `deleted_at`
  - `deleted_by`
  - `cleanup_pending=true`
  - `cleanup_error=""`
- `_persist_document_and_index()`

第二段：
- 调 `storage_service.cleanup_resources()` 清理资源
- 再次加锁读取文档
- 把清理结果回写进 `file_meta`
- 再 `_persist_document_and_index()`
- 刷新 list/detail cache

这说明：
- 删除文件不是一个原子动作
- 而是“先标状态，再清资源，再回写结果”

## 7. 文件状态更新的写链路

`update_uploaded_file_processing_state()` 被 worker 调用，流程是：

1. 校验会话存在
2. 加锁读/回填文档
3. 找到目标文件
4. 规范化状态字段
5. 合并 `file_meta_patch`
6. `_persist_document_and_index()`
7. 刷新 list/detail cache
8. 再读一遍文件详情返回

说明：
- 上传文件状态的唯一主写面也是 JSON，而不是旧表

## 8. 删除会话的写链路并不完整

`delete_conversation()` 只做：

- 删除 `conversations` 行
- 删除本地 JSON 文件
- 刷新 list cache
- 失效 detail cache

没有做：
- 删除远端 JSON 对象
- 删除 `conversation_messages`
- 删除 `conversation_files`
- 删除 outbox 任务
- 清理文件资源

因此它更像“删除主入口索引”，不是完整的数据回收。

## 9. gateway ask_stream 如何写会话

### 9.1 `persist_user_request()`

触发条件：
- `context` 存在
- payload 里有合法 `conversation_id`
- payload 里有 `question`

它会调用：
- `add_message(role="user", content=question, metadata={"source":"ask_stream"})`

所以用户问题会被当成普通 user message 追加进 JSON。

### 9.2 `persist_assistant_summary()`

触发条件：
- `context` 存在
- `summary.done_seen` 为真
- 有合法 `conversation_id`
- 有 `assistant_content`

它会构造 assistant message 的 metadata：

- `source`
- `query_mode`
- `references`
- `steps`
- `route`
- `used_files`
- `timings`
- `trace_id`
- `file_selection`
- `done_seen`

然后调用：
- `add_message(role="assistant", ...)`

这意味着 assistant 回答的摘要化结果，也通过同一条 JSON 写路径落会话。

## 10. `get_latest_turn_context()` 为什么重要

这不是公开 API，但它说明 gateway 读取会话时真正关心什么。

它会从最近一条 assistant message 的 metadata 中提取：
- `route`
- `used_files[].file_id`
- `trace_id`

最终返回：
- `conversation_id`
- `last_turn_route`
- `last_focus_file_ids`
- `trace_id`

也就是说，conversation 在运行态里不只是存历史文本，还承担：
- 上一轮路由决策回放
- 焦点文件集合回放
- trace 串联

## 11. 为什么说 conversation 已经和 gateway 深耦合

因为 ask_stream 至少把下面这些运行态结构直接落到了会话消息 metadata：

- 查询模式
- 参考资料
- 推理步骤
- 路由结果
- 使用到的文件
- timing
- trace_id
- 文件选择结果

这代表：
- conversation 消息已经不只是聊天文案
- 而是带执行轨迹摘要的运行记录

## 12. 写路径的总体特征

综合起来，这个模块的写路径有几个特点：

- 以单会话锁保证顺序
- 以 JSON 主文档承载聚合态
- 以 `conversations` 维护索引和摘要
- 以缓存刷新保证前端读取一致
- 以 outbox 处理远端镜像补偿
- 以 gateway 钩子把运行态写回会话

所以后续如果真的要把公共能力拆服务，`conversation` 最大的难点不是接口数量，而是：
- 业务主写路径
- 运行态钩子
- 补偿逻辑

这三者现在全压在一个 service 里。
