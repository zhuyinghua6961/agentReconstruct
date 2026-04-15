# Frontend Vue Nginx Gateway Design

**Date:** 2026-04-15

## Summary

本设计定义当前仓库的 `frontend-vue` 生产托管方案：

1. 前端继续由 `frontend-vue` 产出静态构建结果
2. Nginx 直接把 [frontend-vue/dist](/home/cqy/worktrees/highThinking/frontend-vue/dist) 当作静态根目录
3. `/api` 和 `/health` 统一反代到 `gateway`，默认上游为 `127.0.0.1:8101`
4. Nginx 必须保持现有 SSE 流式输出行为，不允许代理缓冲破坏 `ask_stream`
5. Nginx 托管后必须继续保留“刷新后任务恢复不中断”的现有能力

这里的“刷新后任务恢复不中断”不是要求浏览器整页刷新时原 TCP/HTTP 连接不断开。浏览器刷新时旧连接一定会断。真实目标是：

1. gateway 里的任务继续执行
2. 前端保留 `task_id` 与 `after_seq`
3. 刷新后重新连接 `/api/v1/tasks/{task_id}/events?after_seq=...`
4. 事件流能够续接，不重复关键事件，也不丢终态

当前仓库已经实现这套恢复模型。Nginx 部署不能破坏它。

## Scope

本设计覆盖：

1. `deploy/nginx/` 下的前端托管 Nginx 模板与说明
2. 顶层 `scripts/` 下的前端构建、Nginx 启停、状态查看、验证脚本
3. 与该部署方式相关的文档说明
4. 对 SSE、超时、刷新恢复的显式验证流程

本设计不覆盖：

1. `frontend-vue` 页面逻辑重构
2. `gateway` 业务逻辑改写
3. `fastQA`、`highThinkingQA`、`public-service`、`patent` 的接口语义变更
4. system-wide Nginx 安装或 systemd 服务接管
5. Docker 化新部署链路

## Hard Boundaries

以下边界是强约束：

1. Nginx 静态根目录必须直接指向 [frontend-vue/dist](/home/cqy/worktrees/highThinking/frontend-vue/dist)，不创建第二份前端发布副本。
2. `/api` 只反代到 `gateway`，不在 Nginx 层把请求拆回各个后端。
3. Nginx 不得启用会破坏流式输出的代理缓冲、请求缓冲或压缩策略。
4. Nginx 部署不能要求 sudo，也不能依赖修改系统 `/etc/nginx/`。
5. gateway 当前运行模型保持不变。当前线上实例确认是 Gunicorn `16` workers，Nginx 方案不能通过把 gateway 降成单 worker 来“换取恢复稳定性”。
6. 刷新恢复能力必须按现有 task replay 机制保留，而不是依赖连接不断开。
7. 如果 gateway 处于多 worker 模式，则共享任务状态必须继续依赖 Redis；Nginx 不能掩盖 shared-state 缺失问题。

## Current State

### 1. 前端当前是 Vite 开发代理模型

[frontend-vue/vite.config.js](/home/cqy/worktrees/highThinking/frontend-vue/vite.config.js) 当前行为：

1. 本地开发监听 `5173`
2. `/api` 和 `/health` 通过 Vite proxy 转发到 `BACKEND_PROXY_TARGET`
3. 默认目标是 `http://127.0.0.1:8101`

这说明前端天然是按“gateway 作为唯一后端入口”设计的。

### 2. 前端 API 调用已兼容相对路径生产部署

[frontend-vue/src/services/api.js](/home/cqy/worktrees/highThinking/frontend-vue/src/services/api.js) 当前行为：

1. 默认 `API_BASE` 为空字符串
2. 所有 API 默认请求相对路径 `/api/...`
3. `ask_stream` 使用 `fetch()` + `ReadableStream` 逐帧解析 `text/event-stream`
4. refresh-survivable 模式使用：
   - `POST /api/v1/tasks`
   - `GET /api/v1/tasks/{task_id}/events?after_seq=N`

这意味着 Nginx 生产托管无需改前端 API 基址，只需保证同域路径可用。

### 3. gateway 已经承担唯一 API 入口与流式转发

[gateway/app/routers/qa.py](/home/cqy/worktrees/highThinking/gateway/app/routers/qa.py) 当前行为：

1. `/api/{mode}/ask` 与 `/api/{mode}/ask_stream` 统一由 gateway 转发
2. 流式响应使用 `StreamingResponse`
3. 响应头已显式带 `X-Accel-Buffering: no`
4. gateway 不直接把前端挂在自己进程里

### 4. highThinkingQA 本身也已按 SSE 正确返回

[highThinkingQA/server_fastapi/routers/ask.py](/home/cqy/worktrees/highThinking/highThinkingQA/server_fastapi/routers/ask.py) 当前行为：

1. `ask_stream` 返回 `StreamingResponse`
2. `media_type="text/event-stream"`
3. 响应头包含：
   - `Cache-Control: no-cache`
   - `Connection: keep-alive`
   - `X-Accel-Buffering: no`
4. 后端按 `SSE_HEARTBEAT_SECONDS` 发送 heartbeat

### 5. 当前 refresh-survivable 能力已经存在

[frontend-vue/src/stores/chatStore.js](/home/cqy/worktrees/highThinking/frontend-vue/src/stores/chatStore.js) 与 [gateway/app/services/qa_tasks.py](/home/cqy/worktrees/highThinking/gateway/app/services/qa_tasks.py) 当前已经形成这条链路：

1. 前端保存 `activeTask` 与 `lastTaskSeq`
2. 刷新或切换回来后，前端根据 `task_id` 和 `after_seq` 续订阅事件
3. gateway 任务接口支持按 `after_seq` 回放事件
4. 流结束后前端会清理 `activeTask`

### 6. gateway 当前是 16 worker 运行

实际宿主机进程已确认：

1. gateway 当前运行命令带 `--workers 16`
2. [gateway/scripts/start_gunicorn.sh](/home/cqy/worktrees/highThinking/gateway/scripts/start_gunicorn.sh) 默认也是 `GATEWAY_GUNICORN_WORKERS=16`
3. [resource/config/services/gateway/config.shared.env](/home/cqy/worktrees/highThinking/resource/config/services/gateway/config.shared.env) 当前也设置了 `GATEWAY_GUNICORN_WORKERS=16`

因此本设计不能假设 gateway 是单 worker。

### 7. 多 worker 下刷新恢复依赖 shared state

[gateway/app/main.py](/home/cqy/worktrees/highThinking/gateway/app/main.py)、[gateway/app/services/execution_event_relay.py](/home/cqy/worktrees/highThinking/gateway/app/services/execution_event_relay.py)、[gateway/app/services/execution_queue_status.py](/home/cqy/worktrees/highThinking/gateway/app/services/execution_queue_status.py) 表明：

1. gateway 的任务状态和事件回放支持 Redis 存储
2. Redis 不可用时会退回 memory fallback
3. 多 worker 下如果退回 memory fallback，刷新恢复将不再可靠

因此部署验收必须把 shared Redis 视为多 worker refresh-survivable 的依赖条件。

## Goals

1. 为当前仓库提供一套可直接使用的、纯用户态的 Nginx 托管方案。
2. 前端构建后，Nginx 直接读取 [frontend-vue/dist](/home/cqy/worktrees/highThinking/frontend-vue/dist) 的最新内容。
3. `/api` 和 `/health` 同域反代到 gateway，保持当前前后端交互协议不变。
4. `ask_stream` 的流式首帧、heartbeat、done/error 终帧不能被 Nginx 缓冲或合并到失去实时性。
5. 刷新后任务恢复能力必须保持可用。
6. 提供可重复执行的构建、启动、停止、状态查看、验证脚本。

## Non-Goals

1. 不把 gateway 内嵌到 Nginx/OpenResty 里。
2. 不重写前端的任务恢复逻辑。
3. 不引入 WebSocket 替代 SSE。
4. 不处理 TLS、域名、反向代理链上更高层 LB 的生产化细节。
5. 不自动安装 Nginx 二进制。

## Options Considered

### Option A: Nginx 直接指向 `frontend-vue/dist`

优点：

1. 满足“build 完立即可被 Nginx 读取”的目标
2. 不需要额外同步或复制
3. 目录最少，发布链路最清晰

缺点：

1. `dist` 未构建时页面不可用
2. Nginx 对构建结果和工作树目录结构有直接依赖

结论：

推荐方案。

### Option B: build 后同步到单独发布目录

优点：

1. 发布目录和源码目录解耦
2. 可做双目录切换

缺点：

1. 与“直接指向 build 目录”的用户目标相反
2. 多一次同步步骤，增加发布复杂度

结论：

不采用。

### Option C: 保持 Vite preview / dev server，Nginx 只做代理

优点：

1. 改动最少

缺点：

1. 不是稳定的静态发布形态
2. 不能满足“打成 Nginx”的目标

结论：

不采用。

## Recommended Design

## 1. 目录与产物布局

新增和约定的主要产物：

1. `deploy/nginx/frontend-vue-gateway.nginx.conf.template`
2. `scripts/build_frontend.sh`
3. `scripts/start_nginx_frontend.sh`
4. `scripts/stop_nginx_frontend.sh`
5. `scripts/status_nginx_frontend.sh`
6. `scripts/test_nginx_frontend.sh`

路径约定：

1. 前端构建产物目录：`/home/cqy/worktrees/highThinking/frontend-vue/dist`
2. 默认 Nginx 运行根：`/home/cqy/worktrees/highThinking/resource/runtime/dev/frontend-nginx`
3. 默认 Nginx 日志根：`/home/cqy/worktrees/highThinking/resource/logs/dev/frontend-nginx`
4. 若 `resource/` 不可用，则回退到仓库内 `.runtime/frontend-nginx`

## 2. Nginx 运行方式

Nginx 采用用户态 prefix 方式启动，不依赖系统配置目录：

1. 通过 `nginx -p <runtime_root> -c <rendered_conf>` 启动
2. `pid`、`client_body_temp`、访问日志、错误日志都写到用户可写目录
3. 启动脚本负责把模板渲染为运行态配置文件

脚本必须支持以下环境变量：

1. `NGINX_BIN`
2. `FRONTEND_NGINX_PORT`
3. `GATEWAY_UPSTREAM_URL`
4. `FRONTEND_DIST_DIR`
5. `NGINX_RUNTIME_ROOT`
6. `NGINX_LOG_ROOT`

默认值：

1. `FRONTEND_NGINX_PORT=9093`
2. `GATEWAY_UPSTREAM_URL=http://127.0.0.1:8101`
3. `FRONTEND_DIST_DIR=/home/cqy/worktrees/highThinking/frontend-vue/dist`

`9093` 只是默认值，不是协议常量。目的是避免和已存在的开发端口冲突，同时不占用 root 端口。

## 3. 静态文件托管策略

Nginx 的静态部分应采用：

1. `root <frontend_dist_dir>`
2. `index index.html`
3. `location / { try_files $uri $uri/ /index.html; }`

可选但推荐的静态优化：

1. `index.html` 禁长期缓存
2. `assets/` 下哈希资源允许长缓存

这些优化不能改变前端路由行为。

## 4. Gateway 反代策略

必须反代的路径：

1. `/api/`
2. `/health`

代理头必须包含：

1. `Host`
2. `X-Real-IP`
3. `X-Forwarded-For`
4. `X-Forwarded-Proto`

代理协议参数必须包含：

1. `proxy_http_version 1.1`
2. `proxy_set_header Connection ""`
3. `proxy_connect_timeout 5s`
4. `proxy_read_timeout 3600s`
5. `proxy_send_timeout 3600s`
6. `send_timeout 3600s`

这里把 Nginx timeout 设为显著大于 gateway 当前 `600s`，目的是避免 Nginx 成为更早的中断点。

## 5. SSE 保护策略

Nginx 模板必须显式包含以下流式保护：

1. `proxy_buffering off`
2. `proxy_request_buffering off`
3. `gzip off`
4. `add_header X-Accel-Buffering no always`

设计理由：

1. 后端虽然已经返回 `X-Accel-Buffering: no`，但代理层仍应显式关闭自身缓冲
2. `ask_stream` 与任务事件流都依赖及时 flush
3. 刷新恢复重新订阅时，前端需要尽快拿到 replay frame，不能被代理层攒包

Nginx 不区分“普通 API”与“流式 API”做不同缓冲策略，统一禁用代理缓冲。这样配置更稳，代价是放弃一部分代理吞吐优化，但与本项目需求一致。

## 6. Refresh-Survivable 保证

Nginx 不能也不需要保证浏览器刷新时旧连接不断开。真正要保证的是：

1. `POST /api/v1/tasks` 可正常创建任务
2. `GET /api/v1/tasks/{task_id}/events?after_seq=N` 可正常透传
3. 查询参数 `after_seq` 不被改写、缓存或吞掉
4. 断开重连后的 replay frame 顺序、终态事件、去重语义保持不变

因此 Nginx 方案的判断标准是：

1. 任务运行时刷新页面
2. 前端拿旧 `task_id` 与最新 `last_seq`
3. 新连接继续收到后续事件
4. 不出现整段结果丢失、终态缺失、明显重复帧

## 7. Shared Redis 作为多 Worker 前提

由于 gateway 当前是 `16` workers，本设计明确依赖：

1. `REDIS_ENABLED=1`
2. `GATEWAY_REFRESH_SURVIVABLE_QA_TASKS_ENABLED=1`
3. gateway 共享 `ExecutionQueueStatusStore`
4. gateway 共享 `ExecutionEventRelayStore`

如果 Redis 不可用：

1. gateway 会回退 memory fallback
2. 多 worker 下 refresh-survivable 语义将失去可靠性
3. Nginx 本身无法修复该问题

因此验证脚本与运维说明中必须把 “Redis live available” 视为前置检查项。

## 8. 脚本设计

### `scripts/build_frontend.sh`

职责：

1. 进入 `frontend-vue`
2. 执行 `npm run build`
3. 明确输出 `dist` 目录位置

### `scripts/start_nginx_frontend.sh`

职责：

1. 解析默认路径和环境变量
2. 校验 `frontend-vue/dist` 是否存在
3. 渲染 Nginx 模板
4. 先执行 `nginx -t`
5. 通过用户态 prefix 启动 Nginx
6. 输出端口、PID、日志路径、静态根目录、gateway 上游地址

### `scripts/stop_nginx_frontend.sh`

职责：

1. 基于 pid 文件执行停止
2. 清理陈旧 pid
3. 报告是否停止成功

### `scripts/status_nginx_frontend.sh`

职责：

1. 报告 Nginx 是否运行
2. 输出 pid、端口、配置文件、日志路径
3. 输出 `dist` 与 gateway 上游配置

### `scripts/test_nginx_frontend.sh`

职责：

1. 测静态首页是否可访问
2. 测 SPA 刷新路径是否回落到 `index.html`
3. 测 `/health` 与基础 `/api` 代理是否可达
4. 测流式首帧和 heartbeat 是否按时出现
5. 测任务流断开后通过 `after_seq` 是否可重连回放

该脚本应允许通过环境变量传入认证信息，例如：

1. `BASE_URL`
2. `AUTH_BEARER_TOKEN`
3. `TASK_REQUEST_PAYLOAD_FILE`

## 9. Verification Plan

实现完成后的验证必须覆盖以下层级。

### A. 静态与路由

1. `frontend-vue` 构建成功
2. Nginx 配置语法校验成功
3. `GET /` 返回前端首页
4. `GET /nonexistent-route` 仍返回前端首页

### B. 基础代理

1. `GET /health` 可通
2. `GET /api/healthz` 或当前健康接口可通
3. 常规 JSON API 不受影响

### C. SSE 实时性

1. `ask_stream` 首帧在合理时间内出现
2. 长思考期间 heartbeat 能持续到达
3. 终态 `done` 或 `error` 事件能正常抵达

### D. Refresh-Recovery

必须模拟：

1. 创建 task
2. 首次连接到 `/api/v1/tasks/{task_id}/events`
3. 读取若干事件后主动断开
4. 以 `after_seq=<latest_seq>` 再次连接
5. 验证剩余事件能继续输出且不重复关键终态

### E. Shared-State 前置检查

在执行 refresh-recovery 验证前，必须确认 gateway 健康信息中：

1. Redis enabled
2. Redis live available
3. refresh-survivable tasks enabled

否则验证结果无意义。

## 10. Failure Handling

以下失败必须显式报错，而不是静默降级：

1. `dist` 不存在
2. Nginx 配置渲染失败
3. `nginx -t` 失败
4. 端口冲突
5. gateway 不可达
6. Redis 未启用但用户要求验证 refresh recovery

## 11. Risks

### Risk 1: Nginx 启动成功但 `dist` 为空

影响：

1. 页面空白或只返回目录错误

缓解：

1. 启动脚本在启动前校验 `index.html`

### Risk 2: 代理缓冲误开导致流式退化

影响：

1. `ask_stream` 不再逐帧刷新
2. 前端看起来像“转圈很久后一次性出结果”

缓解：

1. 模板显式禁用缓冲与压缩
2. 验证脚本检查首帧延迟与 heartbeat

### Risk 3: 多 worker + 无 Redis 时 refresh recovery 假通过

影响：

1. 单次手工测试可能偶然命中同 worker
2. 实际刷新恢复不稳定

缓解：

1. 验证前先检查 Redis live available
2. 在文档中把 shared Redis 写成前置条件，不模糊描述

## 12. Implementation Notes

实现时应优先保持“可落地、可测、可回退”：

1. 模板渲染优先于把绝对路径硬编码进 Git 里的最终配置
2. 启动脚本优先做校验，再启动
3. 验证脚本优先覆盖 Nginx 真正可能破坏的行为，不去重复后端已有单测

## 13. Acceptance Criteria

满足以下条件时，本设计视为实现成功：

1. 执行前端构建后，Nginx 可直接托管最新 `dist`
2. `/api` 与 `/health` 经 Nginx 访问正常
3. `ask_stream` 通过 Nginx 访问时保持流式实时输出
4. gateway 继续保持现有 16-worker 运行模型
5. 刷新后任务恢复链路通过 Nginx 访问仍可用
6. 启停、状态、测试脚本可重复使用
