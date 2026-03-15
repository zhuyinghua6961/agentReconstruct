# highThinking

当前仓库只保留两部分：
- `frontend-vue/`：Vue 3 + Vite 前端
- `server_fastapi/`：FastAPI + Gunicorn 后端入口

后端运行依赖仍保留在以下目录：
- `server/`：业务服务、数据库、鉴权、存储、SSE、会话持久化
- `agent_core/`：问答 Agent 主流程
- `ingest/`、`retriever/`、`prompts/`：文献入库、检索和提示词
- `config.py`、`env_loader.py`：配置与环境加载

## 运行方式

### 后端
```bash
bash scripts/start_fastapi_gunicorn.sh
bash scripts/status_fastapi_gunicorn.sh
bash scripts/stop_fastapi_gunicorn.sh
```

默认监听：`http://0.0.0.0:8008`

### 前端
```bash
cd frontend-vue
npm install
npm run dev
```

默认端口：`5174`

## 主要接口
- `GET /api/v1/health`
- `POST /api/v1/ask`
- `POST /api/v1/ask_stream`
- `GET /api/v1/view_pdf/{doi}`

## 测试
```bash
pytest tests/test_ask_service_executor.py tests/test_run_agent_overlap.py tests/test_checker_precheck.py -q
```

## 说明
- Flask 旧入口、CLI 入口和迁移残留已移除。
- 运行期数据目录通过 `.gitignore` 排除，不纳入版本控制。
