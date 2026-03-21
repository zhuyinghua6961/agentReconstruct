# Public Service Backend

这个目录是“公共服务独立后端”的实现工作区。

当前阶段目标：
- 先把独立 FastAPI app 骨架搭起来。
- 按模块把现有单体里的公共能力迁进来。
- 做到各模块只差接到 gateway 调度。

当前包含：
- 独立 `app.main`
- 独立 `core` 配置、日志、错误处理、runtime
- 已迁移公共能力模块：
  - `system`
  - `auth`
  - `quota`
  - `conversation`
  - `uploads`
- 已迁移模块：
  - `admin_users`
  - `documents`
- 基础与迁移回归测试

当前迁移进度：
- `system` 已完成当前阶段真实迁移：
  - `health`
  - `background_status`
  - `kb_info`
  - `refresh_kb`
  - `clear_cache`
  - `conversation cache debug`
- `auth` 已完成当前阶段真实迁移：
  - request schemas
  - token service
  - auth deps
  - password policy / login / reset / security questions service logic
  - MySQL repository
  - app 启动期 wiring
  - API / deps 使用当前 live auth service
- `quota` 已完成当前阶段真实迁移：
  - request schemas
  - quota service core model
  - quota deps / precheck / finalize
  - quota management API contract
  - MySQL repository
  - Redis config/override/list cache
  - app 启动期 wiring
  - API / deps 使用当前 live quota service
- `conversation` 已完成当前阶段真实迁移：
  - 会话 CRUD
  - 消息追加
  - JSON 主文档
  - Redis list/detail cache
  - 文件元数据 list/detail/delete/download
  - 对象存储镜像失败 outbox 退化
  - app 启动期 wiring
- `uploads` 已完成当前阶段首批真实迁移：
  - PDF/Excel 上传入口
  - 本地落盘
  - 对象存储 mirror
  - conversation 文件登记
  - `clear_pdf` 兼容接口
- `admin_users`、`documents` 已具备可调用实现

当前不包含：
- gateway 调度接入
- `admin_users`、`documents` 的真实业务实现
- 任何 QA 执行逻辑

建议本地启动方式：

```bash
PUBLIC_SERVICE_ENV_FILES=/home/cqy/worktrees/highThinking/public-service/config.shared.env:/home/cqy/worktrees/highThinking/public-service/config.secret.env \
  conda run --no-capture-output -n agent gunicorn -k uvicorn.workers.UvicornWorker app.main:app --chdir /home/cqy/worktrees/highThinking/public-service/backend --bind 0.0.0.0:8102 --workers 1 --timeout 600
```

如果使用单文件配置：

```bash
cp /home/cqy/worktrees/highThinking/public-service/config.env.example /home/cqy/worktrees/highThinking/public-service/config.env
PUBLIC_SERVICE_ENV_FILE=/home/cqy/worktrees/highThinking/public-service/config.env \
  conda run --no-capture-output -n agent gunicorn -k uvicorn.workers.UvicornWorker app.main:app --chdir /home/cqy/worktrees/highThinking/public-service/backend --bind 0.0.0.0:8102 --workers 1 --timeout 600
```

说明：
- 当前仍是分模块迁移中的独立后端。
- `system`、`auth`、`quota`、`conversation`、`uploads` 已具备当前阶段可调用实现，并已通过回归测试。
- 当前测试状态以 `backend/tests/` 回归结果为准。
- 运行时向量库应放在 `PUBLIC_SERVICE_DATA_ROOT/vector_database`，不要再依赖其他 worktree 的绝对路径。
