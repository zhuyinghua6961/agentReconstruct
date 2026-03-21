# storage 论文 PDF 缓存、下载与回填

对应代码：
- `backend/app/modules/storage/service.py`
- `backend/app/modules/storage/paper_storage.py`
- `backend/app/modules/documents/service.py`
- `backend/app/modules/documents/reference_preview.py`
- `backend/tests/test_storage.py`

## 1. 论文 PDF 是 storage 里最强领域化的一部分

`StorageService` 里专门有：

- `build_paper_filename()`
- `build_paper_object_name()`
- `paper_exists()`
- `ensure_local_paper_pdf()`

命名规则非常固定：

- DOI 里的 `/` 替换成 `_`
- 文件名形如 `<doi-with-underscore>.pdf`
- 对象名形如 `papers/<filename>.pdf`

所以 storage 在这里已经不只是通用文件层，而是带了论文领域语义。

## 2. `paper_exists()` 的优先级是 MinIO 先、本地后

逻辑是：

1. 先通过 factory 拿 backend
2. 如果 backend 是 `MinIOStorageBackend`
3. 尝试 `object_exists(papers/<doi>.pdf)`
4. 如果存在直接返回 `True`
5. 如果失败或不存在，再看本地文件

因此它判断的是：

- “平台能否拿到该 DOI 的 PDF”

而不是：

- “本地 papers 目录里是否有这个文件”

## 3. `ensure_local_paper_pdf()` 的目标不是判断存在，而是确保本地可读

输入：

- `doi`
- `papers_dir`
- `project_root`
- `logger`

输出：

- 返回一个本地 `Path`
- 或返回 `None`

它的真实语义是：

- 不管远端还是本地，最终都要给调用方一份本地可读 PDF 路径

这正是 documents、generation pipeline 这些调用链真正需要的能力。

## 4. 当 backend 是 MinIO 时，优先使用对象存储作为来源

`ensure_local_paper_pdf()` 在 MinIO 场景下的顺序：

1. 先看本地是否已有缓存
2. 如果本地有，再看 MinIO 是否存在对象
3. 若 MinIO 丢了对象，则把本地文件 mirror 回去
4. 本地没有时，创建同目录临时文件
5. 如果远端对象存在并下载成功，就 `os.replace()` 提升为正式文件

这说明当前论文 PDF 的源模型是：

- MinIO 作为优先事实来源
- 本地作为读取缓存与兜底

## 5. 本地已有、远端丢失时会自动回填对象

这是这条链里最关键、也最容易忽略的行为。

如果：

- 本地 `papers/<doi>.pdf` 已经存在
- backend 是 MinIO
- 但远端对象不存在

代码会：

- `mirror_file(local -> minio)`

而且这里用的是一个空实现 logger 兜底，不会因为没传 logger 就报错。

所以系统明确想保持：

- MinIO 和本地缓存之间的最终一致性

至少在论文 PDF 这条链上是这样。

## 6. 下载用临时文件 + promote，避免半成品污染

当需要从 MinIO 下载时：

- 先 `mkstemp(..., dir=local_path.parent)`
- 下载到 `<stem>.<suffix>.tmp`
- 成功后 `os.replace(tmp, local)`
- finally 中再尝试删残余 tmp

这意味着：

- 下载失败不会留下一个看起来像正式 PDF 的坏文件
- 成功替换是原子提升语义

## 7. 针对每个本地 PDF 路径都有线程锁

`_get_paper_download_lock(local_path)` 维护了：

- 全局 dict 的 `threading.Lock`

key 用的是：

- `local_path.resolve()` 后的字符串

这条锁保护的是：

- 同一 DOI 同一路径的本地缓存下载/回填过程

避免多个请求并发下载同一论文时互相覆盖或重复创建临时文件。

## 8. documents 模块是这条能力的主消费方

`DocumentsService._ensure_local_pdf()` 直接调用：

- `storage_service.ensure_local_paper_pdf(...)`

其上游能力包括：

- `view_pdf`
- `summarize_pdf`
- `extract_pdf_text`
- `check_pdf`

`reference_preview.py` 也会用：

- `storage_service.paper_exists(...)`

来判断某 DOI 是否可预览 PDF。

这说明论文 PDF 能力现在已经被 documents 收编为基础设施，而不是 documents 自己再写一套文件定位逻辑。

## 9. 但并不是所有 PDF 链路都迁到了新 service

一些生成式 pipeline 仍在直接用 legacy：

- `modules/storage/paper_storage.py`
- 或通过 `services/storage/paper_storage.py` shim

所以当前论文 PDF 存储能力是双入口并存，不是完全单入口。

## 10. 这条链的真正公共能力形态

从平台角度看，这部分提供的不是：

- “给我一个对象 URL”

而是：

- “给我一个 DOI，我返回一份本地可读 PDF，并尽量把对象存储和本地缓存修正到可用状态”

这就是它为什么既属于 storage，又明显带论文语义。
