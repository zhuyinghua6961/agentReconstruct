# conversation 上传文件处理状态机

对应代码：
- `backend/app/modules/conversation/upload_processing_worker.py`
- `backend/app/modules/conversation/service.py`

## 1. 这个 worker 管什么

`UploadProcessingWorker` 负责把刚上传到会话里的文件，从“只是挂上来了”推进到“可以被后续运行态消费”的状态。

它不负责：
- 文件本体上传
- conversation 文件记录初次创建

它负责：
- 解析 PDF / 表格文件
- 回写解析元数据
- 推进状态机
- 标记失败

## 2. 初始状态从哪里来

`ConversationService.add_uploaded_file()` 创建 JSON 文件项时，初始状态固定为：

- `file_status = active`
- `parse_status = uploaded`
- `index_status = pending`
- `processing_stage = uploaded`
- `last_error = ""`
- `file_meta = {}`

所以 worker 的输入前提是：
- 文件记录已存在
- JSON 文档里已经有该 `file_id`

## 3. worker 的并发控制

并发粒度是：
- `(user_id, conversation_id, file_id)`

内部用：
- `ThreadPoolExecutor`
- `_active_keys` 集合去重

效果：
- 同一个文件不会被并发重复提交
- 不同文件可以并行处理

环境变量：
- `UPLOAD_FILE_PROCESSING_ENABLED`
- `UPLOAD_FILE_PROCESSING_MAX_WORKERS`
- `UPLOAD_FILE_PROCESSING_MAX_PDF_PAGES`

## 4. 状态推进顺序

`_run_task()` 的固定流程：

1. `parse_status=parsing`
   `processing_stage=parsing`
2. 执行解析
3. `parse_status=parsed`
   `processing_stage=parsed`
   回写解析元数据
4. `index_status=indexing`
   `processing_stage=indexing`
5. `index_status=ready`
   `processing_stage=ready`
   回写索引元数据

任何一步抛错：
- `parse_status=failed`
- `index_status=failed`
- `processing_stage=failed`
- `file_meta.processing_failed=true`
- `last_error=<异常文本>`

## 5. 解析支持的文件类型

worker 接受的 `file_type` 只有两类：

- `pdf`
- `excel`

这里的 `excel` 实际包含两种本地文件：
- `.csv`
- Excel 工作簿文件

如果传入其他类型：
- 直接抛 `unsupported upload file_type`

## 6. PDF 解析

PDF 路径会调用外部注入的：
- `extract_pdf_text_fn`

调用参数：
- `max_pages=self._config.pdf_max_pages`
- `exclude_references=False`

成功后回写到 `file_meta`：
- `source_path`
- `parsed_char_count`
- `parsed_preview`

失败条件包括：
- 未注入解析器
- 返回值不是字符串
- 返回以 `[错误]` 开头
- 解析结果为空

## 7. CSV 解析

CSV 读取逻辑比较轻量：

- 第一行作为列头
- 统计后续 `row_count`
- 最多保留 3 行 `sample_rows`
- 每行最多取前 20 个单元格

回写字段：
- `table_format=csv`
- `row_count`
- `column_count`
- `columns`
- `sample_rows`

## 8. Excel 解析

Excel 解析依赖 `pandas`：
- `pd.read_excel(file_path, nrows=20)`

注意：
- 这里只读前 20 行
- 所谓 `row_count_estimate` 实际就是 `len(head_df)`
- 不是整份文件的准确总行数

回写字段：
- `table_format=excel`
- `row_count_estimate`
- `column_count`
- `columns`
- `sample_rows`

## 9. “索引”现在其实是占位状态

worker 在解析完成后会先把文件设成：
- `index_status=indexing`

然后立刻写成：
- `index_status=ready`
- `processing_stage=ready`

同时补两个元数据：
- `index_mode=deferred`
- `index_note=runtime_query_indexing`

这说明当前代码里的“索引”并没有真正做离线建索引，而是：
- 用状态字段表达“后续查询时再做运行态索引/处理”

所以这里更准确的理解应该是：
- `ready` 表示“已完成基础解析，可交给运行时使用”
- 而不是“已生成完备离线索引”

## 10. 状态更新如何回写

worker 不直接改 DB，而是统一调用：
- `conversation_service.update_uploaded_file_processing_state()`

这个 service 会：
- 在单会话锁内更新 JSON 文件项
- 合并 `file_meta_patch`
- 自动规范化 `parse_status / index_status`
- 刷新 detail/list cache

如果更新时碰到短暂 `NOT_FOUND`：
- worker 会再等 150ms 重试一次

这个细节说明作者考虑过“上传记录刚写完，异步任务抢先执行”的竞争窗口。

## 11. 失败语义

失败时的几个事实：

- 失败状态落在 JSON 文档里
- 旧表 `conversation_files` 不会保存这些丰富状态
- `last_error` 是直接保存异常文本
- 下一次如果想重试，需要外部再次 submit

也就是说：
- 这个 worker 没有自带任务队列和自动重试
- 它只是一个线程池驱动的状态推进器

## 12. 这个状态机在公共能力里的意义

它把“用户上传了文件”拆成了两个阶段：

- 文件登记成功
- 文件解析就绪

这对公共能力很重要，因为后续 `documents` / `ask_stream` 真正消费文件时，依赖的是：
- 是否有可读 preview / 表结构
- 当前 `processing_stage` 是否已经到 `ready`

因此上传能力和会话文件能力虽然分在不同模块，真正的文件消费状态机是落在 `conversation` 里的。
