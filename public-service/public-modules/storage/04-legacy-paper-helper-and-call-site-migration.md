# storage legacy helper、shim 与迁移未收口点

对应代码：
- `backend/app/modules/storage/paper_storage.py`
- `backend/app/services/storage/paper_storage.py`
- `backend/app/services/pdf_loader.py`
- `backend/app/modules/generation_pipeline/context_loading.py`
- `backend/app/modules/generation_pipeline/pdf_pipeline.py`
- `backend/app/modules/storage/service.py`

## 1. 仓库里同时存在新旧两套论文 PDF 入口

新的入口：

- `app.modules.storage.service.storage_service.ensure_local_paper_pdf`

旧的 helper：

- `app.modules.storage.paper_storage.ensure_local_paper_pdf`

兼容 shim：

- `app.services.storage.paper_storage`
- 内容只有 `from app.modules.storage.paper_storage import *`

这说明 storage 迁移是渐进式的，还没完全把调用面收敛到 `storage_service`。

## 2. legacy helper 直接读环境变量并自己建 MinIO client

`modules/storage/paper_storage.py`：

- 自己读 `MINIO_ENDPOINT`
- `MINIO_ACCESS_KEY`
- `MINIO_SECRET_KEY`
- `MINIO_BUCKET`
- `MINIO_SECURE`

然后自己 `from minio import Minio`

这与新的 `integrations/storage/factory.py` 模型不同。

新模型是：

- 统一从 settings 读配置
- 统一走 backend 抽象

旧模型则是：

- helper 内部直接操作 MinIO SDK

## 3. legacy helper 与新 service 的核心语义相似，但实现不一致

两者都支持：

- 论文文件名规范化
- MinIO 优先
- 本地兜底
- 本地存在时对远端回填
- 针对每个本地路径做锁

但差异也很明确：

新 service：

- 走 `StorageBackend`
- 下载用临时文件后 promote
- 可与 local backend / MinIO backend 统一协作

旧 helper：

- 直接 `stat_object / fget_object / fput_object`
- 没走 backend 抽象
- 本地存在时直接返回
- 下载时直接落目标文件路径

所以它们语义接近，但不是同一实现。

## 4. 仍在使用旧 helper 的链路主要是 agent/generation 侧

当前仍直接 import 旧 helper 的地方包括：

- `app.services.pdf_loader`
- `app.modules.generation_pipeline.context_loading`
- `app.modules.generation_pipeline.pdf_pipeline`

而 documents/reference_preview/conversation 这类更新的公共能力链已经在用：

- `storage_service`

这说明迁移边界大致是：

- 新后端模块 -> 新 storage service
- 老 agent/generation 工具链 -> legacy helper

## 5. `app.services.storage.paper_storage` 是纯兼容路径

这个文件只有一行：

- `from app.modules.storage.paper_storage import *`

作用很明确：

- 兼容历史 import 路径

这不是新的实现层，而是 import alias/shim。

因此如果后面要真正收口 storage 入口，这个 shim 是可以作为迁移完成标志来清理的对象。

## 6. 为什么这件事必须写进公共能力文档

因为如果只看 `modules/storage/service.py`，会误以为：

- 平台已经统一到 `StorageBackend + StorageService`

但实际代码事实是：

- 论文 PDF 的一些重要读取链仍然绕过这套统一入口

这会带来两个后果：

- 配置来源可能分叉
- 行为修复可能只修到一半调用面

## 7. 这块不是“脏代码”，而是迁移中的现实状态

从代码看，legacy helper 并非完全废弃：

- 仍被多个调用点真实使用
- 且 shim 仍然保留

所以当前更准确的结论不是“这段没用了”，而是：

- storage 层完成了一半统一，论文 helper 迁移还没彻底收口

## 8. 对后续重构的启示

如果未来要把 storage 真的抽成统一公共服务，至少要先统一三件事：

- 论文 PDF 本地缓存入口
- MinIO 配置来源
- 调用方 import 路径

不然 storage 的实现虽然统一了，调用面仍然会继续双轨运行。
