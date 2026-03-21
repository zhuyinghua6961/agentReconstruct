# storage 模块代码细读

模块路径：
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

模块定位：
- 明确属于公共能力
- 负责平台文件位置抽象、对象镜像、本地缓存、下载决策与资源清理
- 同时夹带明显的论文 PDF 领域语义

已细拆到：

- `storage/README.md`
- `storage/01-backend-selection-and-storage-ref.md`
- `storage/02-paper-pdf-cache-and-mirror.md`
- `storage/03-conversation-json-download-and-cleanup.md`
- `storage/04-legacy-paper-helper-and-call-site-migration.md`
- `storage/05-runtime-tests-and-frontend-usage.md`

本模块的关键结论：

- storage 的核心不是“选本地还是 MinIO”，而是统一 `storage_ref/local_path` 这层文件位置抽象
- local backend 不是真正的对象存储，只是把本地路径包装成 `local://...` 引用
- 论文 PDF 链使用“MinIO 优先、本地缓存兜底、远端缺失时本地反向 mirror”的策略
- conversation JSON、上传文件下载和资源清理都已经深度依赖 storage service
- 论文 helper 仍存在 legacy 双入口，说明存储层统一迁移还没彻底收口

当前已确认问题与迁移修复点：

- `P2` 论文 PDF 读取仍存在新旧双入口：
  - 新链路走 `backend/app/modules/storage/service.py`
  - 老链路仍可走 `backend/app/modules/storage/paper_storage.py`
  - 还保留了 `backend/app/services/storage/paper_storage.py` shim
- 这说明存储能力虽然已经公共化，但“论文 PDF helper 迁移”还没有真正收口；如果后续只迁 `storage_service` 而遗漏旧 helper 调用点，生成链、agent 链和 PDF 上下文链还会残留旧行为。
- `P3` local backend 只是把本地路径包装成 `local://...` 引用，不是真正的对象存储实现；这不是 bug，但属于很容易在拆公共后端时被误解的设计事实，文档里需要明确保留。

建议阅读顺序：

1. 先看 `storage/01-backend-selection-and-storage-ref.md`
2. 再看 `storage/02-paper-pdf-cache-and-mirror.md`
3. 然后看 `storage/03-conversation-json-download-and-cleanup.md`
4. 如果要判断迁移状态，再看 `storage/04-legacy-paper-helper-and-call-site-migration.md`
5. 如果要看 runtime 和前端可见面，再看 `storage/05-runtime-tests-and-frontend-usage.md`
