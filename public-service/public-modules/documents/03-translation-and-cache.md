# documents 翻译能力与缓存子系统

对应代码：
- `backend/app/modules/documents/translation_service.py`
- `backend/app/modules/documents/translator.py`
- `backend/app/modules/documents/translation_cache_impl.py`
- `backend/app/modules/documents/cache.py`
- `backend/app/modules/documents/service.py`

## 1. documents 的翻译不是简单调用模型

`documents_service.translate()` 实际只是委托：
- `documents_translation_service.translate_batch()`

但这个 translation service 自己已经是一整套子系统：
- translator 单例
- cache 封装
- OpenAI-compatible provider
- 批量失败统计

## 2. `DocumentsTranslationService` 怎么初始化 translator

它懒加载一个 translator 单例：
- 默认类是 `SmartTranslator`
- 默认 OpenAI client 类是 `openai.OpenAI`

还有一个细节：
- 如果 translator 自带的 cache 不是 `DocumentsTranslationCache`
- 会包装成 documents 自己的 cache 外壳

这说明 documents 模块想把“cache surface”收口到自己名下。

## 3. `SmartTranslator` 的启用条件

`SmartTranslator` 读取：
- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`

如果没有 API key：
- `client = None`
- `enabled = False`

这时 `translate_batch()` 会直接返回：
- `503`
- `TRANSLATION_DISABLED`

## 4. 翻译 provider 的真实语义

`SmartTranslator.provider` 固定返回：
- `openai-compatible`

模型名默认是：
- `deepseek-v3.1`

所以 documents 翻译和 summarize 一样，都是：
- OpenAI-compatible chat completion
- 不是统一 runtime LLM pipeline

## 5. 批量翻译逻辑

`translate_batch()` 的流程：

1. 要求 `texts` 是非空 list，否则 `400`
2. translator 未启用则 `503`
3. 逐个处理每个片段
4. 空白文本直接返回空字符串
5. 先查 cache
6. cache miss 再调用 `translator.translate()`
7. 收集：
   - `translations`
   - `failures`
   - `cache_hits`
   - `failed_non_empty_count`

## 6. 失败语义不是全-or-nothing

### 6.1 全部非空片段都失败

返回：
- `502`
- `TRANSLATION_FAILED`

### 6.2 只有部分失败

仍返回：
- `200`
- `success=true`

但 payload 中会带：
- `data.failed_count`
- `data.failures`

这代表：
- documents 翻译允许部分成功
- 调用方不能只看 status code，还要看失败列表

## 7. 返回 payload 有双层 translations

成功时同时会返回：

- 顶层 `translations`
- 顶层 `count`
- 顶层 `cache_hits`
- `data.translations`
- `data.count`
- `data.cache_hits`
- `data.provider`

这是一种明显的兼容式返回结构，前端也确实在做双层兼容解析。

## 8. TranslationCache 的存储模型

缓存路径相关环境变量：
- `TRANSLATION_CACHE_DIR`
- `TRANSLATION_CACHE_MAX_ENTRIES`
- `TRANSLATION_CACHE_REMOTE_SYNC_INTERVAL_SECONDS`
- `TRANSLATION_CACHE_OBJECT_NAME`

默认本地文件：
- `translation_cache/translations.json`

锁文件：
- `translation_cache/.translations.lock`

远端对象名默认：
- `translation_cache/translations.json`

## 9. 缓存 key 和 value 结构

key 不是原文，而是：
- `sha256(text)`

value 不是单纯字符串，而是：
- `translation`
- `updated_at`

因此这是一个“带时间戳的内容缓存”，方便本地与远端 merge。

## 10. MinIO 与本地文件如何协作

TranslationCache 的策略是：

1. 启动时读本地缓存
2. 再读 MinIO 远端缓存
3. 按 `updated_at` 做 merge
4. 写回本地快照
5. 如果远端为空但本地有数据，会回填到 MinIO

后续：
- `get()` 会按间隔尝试从远端 refresh
- `set()` 会强制先 refresh 远端，再写本地，再上传远端

所以这是一个：
- MinIO 优先
- 本地备份
- 双向 merge

的缓存模型。

## 11. 锁与并发控制

缓存层有两层锁：
- 进程内 `threading.RLock`
- 文件锁 `.translations.lock`，如果平台支持 `fcntl`

这说明它考虑的是：
- 多线程并发
- 多进程本地文件一致性

## 12. 一个细节：`get()` 会刷新访问时间，但不立即持久化

`get()` 命中后会：
- `entry["updated_at"] = time.time()`

但不会立刻保存本地快照。

这意味着：
- 访问热度会先停留在内存态
- 下一次 save/merge 时才被稳固到文件和远端

这更像“轻量 LRU 倾向”而不是严格持久访问统计。

## 13. `translator.translate()` 的提示词语义

提示词要求模型：
- 只输出翻译结果
- 不加说明、注释、解释
- 保持专业术语准确

失败时返回的不是异常，而是：
- `❌ 翻译失败: ...`

而 `translate_batch()` 是通过是否以 `❌` 开头来识别失败。

所以这里的失败协议是：
- 字符串约定
- 不是异常类型

## 14. 这套翻译子系统的本质

documents 的翻译能力已经不是“翻一下文本”那么简单，而是：

- provider 封装
- translator 单例
- 本地/远端缓存
- 批量部分失败处理
- 前端兼容双层返回

因此它本身就可以视为 documents 下面一个相对独立的公共子能力。
