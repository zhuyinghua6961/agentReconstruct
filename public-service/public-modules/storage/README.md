# storage 细拆索引

对应代码：
- `backend/app/modules/storage/service.py`
- `backend/app/modules/storage/paper_storage.py`
- `backend/app/modules/storage/schemas.py`
- `backend/app/integrations/storage/base.py`
- `backend/app/integrations/storage/local.py`
- `backend/app/integrations/storage/minio.py`
- `backend/app/integrations/storage/factory.py`
- `backend/app/services/storage/paper_storage.py`
- `backend/app/core/runtime.py`
- `backend/app/modules/conversation/json_store.py`
- `backend/app/modules/conversation/outbox_worker.py`
- `backend/app/modules/conversation/service.py`
- `backend/app/modules/documents/service.py`
- `backend/app/modules/documents/reference_preview.py`
- `backend/app/services/pdf_loader.py`
- `backend/app/modules/generation_pipeline/context_loading.py`
- `backend/app/modules/generation_pipeline/pdf_pipeline.py`
- `backend/tests/test_storage.py`
- `backend/tests/test_real_dependencies_optional.py`
- `frontend-vue/src/services/api.js`

本目录把 `storage` 再拆成 5 个视角：

- `01-backend-selection-and-storage-ref.md`
  说明 storage backend 抽象、factory 选择逻辑、`local://`/`minio://` 引用格式和本地 backend 的真实语义。
- `02-paper-pdf-cache-and-mirror.md`
  说明论文 PDF 命名、MinIO 优先读取、本地缓存、回填 mirror 和并发下载锁。
- `03-conversation-json-download-and-cleanup.md`
  说明 conversation JSON 镜像、下载解析、临时代理文件、资源清理与文件删除语义。
- `04-legacy-paper-helper-and-call-site-migration.md`
  说明 legacy `paper_storage` helper、`app.services.storage.paper_storage` shim、仍在使用旧 helper 的链路。
- `05-runtime-tests-and-frontend-usage.md`
  说明 runtime 启动时的 storage 健康探测、测试覆盖、以及前端如何消费 `storage_ref/local_path`。

总体判断：
- `storage` 是明确的公共能力，但它不是纯粹的通用对象存储层。
- 当前实现同时承担了：
  - 通用 backend 抽象
  - 论文 PDF 本地缓存/对象回填
  - conversation 文件下载与清理
- 因此它更像“文件持久化与分发能力”，不是单纯的 blob SDK。

当前已确认问题：
- 论文 PDF helper 仍存在新旧双入口，迁移没有完全收口。
- local backend 只是引用包装，不是真正对象存储，实现语义容易被误判。
