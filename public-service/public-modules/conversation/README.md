# conversation 细拆索引

对应代码：
- `backend/app/modules/conversation/api.py`
- `backend/app/modules/conversation/service.py`
- `backend/app/modules/conversation/repository.py`
- `backend/app/modules/conversation/json_store.py`
- `backend/app/modules/conversation/cache.py`
- `backend/app/modules/conversation/outbox.py`
- `backend/app/modules/conversation/outbox_worker.py`
- `backend/app/modules/conversation/upload_processing_worker.py`
- `backend/app/modules/conversation/schemas.py`

本目录把 `conversation` 再拆成 6 个视角来读：

- `01-api-and-contracts.md`
  说明公开接口、入参、出参、鉴权、配额和错误契约。
- `02-data-model-and-json-store.md`
  说明 `conversations / conversation_messages / conversation_files` 和 JSON 主文档、对象存储镜像之间的关系。
- `03-cache-and-read-path.md`
  说明 Redis 缓存、会话详情读取、兼容回填、删除文件补偿清理的读路径。
- `04-outbox-and-remote-sync.md`
  说明 JSON 远端镜像、同步失败补偿和 outbox worker 的完整链路。
- `05-upload-processing-state-machine.md`
  说明上传文件从 `uploaded` 到 `ready` 的状态机和 worker 细节。
- `06-gateway-hooks-and-write-path.md`
  说明消息写入主路径、ask_stream/gateway 持久化钩子以及与缓存刷新的关系。

总体判断：
- 这是一个会话聚合子系统，不是简单 CRUD。
- JSON 文档是当前消息与文件聚合态的主文档。
- MySQL、Redis、本地文件、对象存储之间是补偿一致，不是强事务一致。

当前已确认问题：
- 删除会话不会显式清理远端 JSON、副表历史和会话文件资产。
- 当前删除语义更像“删除主索引入口”，不是完整资产回收。
