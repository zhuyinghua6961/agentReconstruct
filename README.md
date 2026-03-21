# highThinking

当前仓库的主联调形态是多服务架构：

- `frontend-vue/`：当前正式前端，Vite dev server 默认 `5173`
- `gateway/`：统一网关后端，默认 `8101`
- `public-service/`：公共能力后端，默认 `8102`
- `fastQA/`：快速问答后端，默认 `8008`
- `highThinkingQA/`：思考问答后端，默认 `8009`
- `resource/`：共享资源、配置、运行态目录

旧的根目录前端已迁出为本地备份，不再作为当前联调入口。

## 运行方式

### 前端
```bash
cd frontend-vue
npm install
npm run dev
```

默认端口：`5173`

开发代理：
- `/api/*` -> `http://127.0.0.1:8101`

### 后端
统一启停脚本：

```bash
bash scripts/start_all.sh
bash scripts/status_all.sh
bash scripts/stop_all.sh
```

也可以单独控制：

```bash
bash scripts/_service_common.sh gateway:start
bash scripts/_service_common.sh public-service:start
bash scripts/_service_common.sh fastQA:start
bash scripts/_service_common.sh highThinkingQA:start
```

## 说明

- 当前正式前端以根目录 `frontend-vue/` 为准。
- `gateway/` 目录现在只保留网关后端代码、脚本、测试和协议文档。
- 运行期数据目录通过 `.gitignore` 排除，不纳入版本控制。
