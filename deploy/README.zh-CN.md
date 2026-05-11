# Docker 离线部署说明

## 目标

`deploy/` 目录是最终交付用的 Docker 部署包，包含以下服务：

- `gateway`
- `public-service`
- `fastQA`
- `highThinkingQA`
- `patentQA`
- `frontend nginx`
- `mysql`
- `redis`
- `minio`

部署目标是：业务镜像、基础组件镜像、数据库初始化脚本、向量库、知识图谱数据、论文原文和专利原文都可以随部署包一起交付到离线机器上运行。目标机器不需要 Conda 环境，也不依赖本机模型文件；模型调用统一通过 HTTP URL 配置。

## 目录说明

- `docker-compose.yml`
  - Docker 编排文件，负责启动 MySQL、Redis、MinIO、后端服务、前端 nginx 和初始化任务。
- `.env.production.example`
  - 推荐交付使用的生产配置模板。
- `.env.example`
  - 较短的示例配置模板。
- `.env`
  - 当前本地打包和验证使用的配置文件，里面的占位密码不能直接作为生产密码。
- `docker/`
  - 各服务镜像的 Dockerfile。
- `mysql-init/001_schema.sql`
  - MySQL 首次启动时自动导入的表结构。
- `minio-init/init.sh`
  - MinIO bucket 初始化脚本。
- `minio-seed/`
  - 启动时自动导入 MinIO 的对象数据，例如论文原文、专利原文。
- `seed-data/`
  - 启动时预置到 Docker volume 的向量库、知识图谱和运行时状态数据。
- `scripts/collect_seed_data.sh`
  - 从当前代码目录收集向量库、知识图谱等 seed-data。
- `scripts/collect_minio_seed.sh`
  - 从当前代码目录收集需要导入 MinIO 的论文和专利原文。
- `scripts/preflight_check.sh`
  - 打包或部署前检查配置、数据目录和 compose 是否可用。
- `scripts/export_images.sh`
  - 导出业务镜像和基础组件镜像，方便离线机器 `docker load`。

## 配置优先级

最终部署时，运行时配置由 `deploy/.env` 注入到 Docker 容器中。

镜像内仍会带着 `resource/config` 下的默认配置，但同名环境变量优先级更高。也就是说：

- 客户最终主要改 `deploy/.env`
- `resource/config` 作为默认值和业务参数兜底
- `.env` 中写了的连接信息会覆盖镜像内默认配置

## 最终需要客户修改的配置

推荐从生产模板生成实际配置：

```bash
cp deploy/.env.production.example deploy/.env
```

然后修改 `deploy/.env`。

### 镜像和端口

需要确认这些变量：

- `GATEWAY_IMAGE`
- `PUBLIC_SERVICE_IMAGE`
- `FASTQA_IMAGE`
- `HIGHTHINKINGQA_IMAGE`
- `PATENT_IMAGE`
- `FRONTEND_IMAGE`
- `FRONTEND_PUBLISH_PORT`
- `GATEWAY_PUBLISH_PORT`
- `PUBLIC_SERVICE_PUBLISH_PORT`
- `FASTQA_PUBLISH_PORT`
- `HIGHTHINKINGQA_PUBLISH_PORT`
- `PATENT_PUBLISH_PORT`
- `MYSQL_PUBLISH_PORT`
- `REDIS_PUBLISH_PORT`
- `MINIO_API_PUBLISH_PORT`
- `MINIO_CONSOLE_PUBLISH_PORT`

### 数据库、中间件和鉴权

需要修改为正式值：

- `MYSQL_ROOT_PASSWORD`
- `MYSQL_DATABASE`
- `MYSQL_APP_USER`
- `MYSQL_APP_PASSWORD`
- `REDIS_PASSWORD`
- `MINIO_ROOT_USER`
- `MINIO_ROOT_PASSWORD`
- `MINIO_BUCKET`
- `JWT_SECRET`
- `PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN`

### 大模型配置

最终部署中，`fastQA`、`highThinkingQA`、`patentQA` 和 `public-service` 共用一套 LLM 配置：

```env
LLM_API_KEY=replace_with_real_llm_key
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen3-max
```

如果客户部署的是内网本地模型服务，也仍然使用这三个变量，只需要把 `LLM_BASE_URL` 改成内网模型服务地址。

如果模型服务不需要 API key，`LLM_API_KEY` 可以留空。

### fastQA 和 patentQA 的 embedding 配置

`fastQA` 和 `patentQA` 共用一套 BGE-compatible embedding。现有向量库是按这套 embedding 建的，默认模型名是 `bge-local`，不是 highThinkingQA 使用的 `text-embedding-v4`：

```env
QA_EMBEDDING_MODEL_TYPE=remote
QA_EMBEDDING_API_KEY=
QA_EMBEDDING_BASE_URL=http://127.0.0.1:8001/v1/embeddings
QA_EMBEDDING_MODEL=bge-local
```

注意：`QA_EMBEDDING_BASE_URL` 这里填写完整的 embeddings 接口地址，通常以 `/v1/embeddings` 结尾。最终 Docker 内如果 embedding 服务也容器化，应改成对应的 Docker 服务名地址，例如 `http://embedding-service:8001/v1/embeddings`。

### highThinkingQA 的 embedding 配置

`highThinkingQA` 的 embedding 独立配置：

```env
HIGHTHINKINGQA_EMBEDDING_API_KEY=
HIGHTHINKINGQA_EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
HIGHTHINKINGQA_EMBEDDING_MODEL=text-embedding-v4
```

注意：这里的 `HIGHTHINKINGQA_EMBEDDING_BASE_URL` 通常填写 OpenAI-compatible base URL，一般以 `/v1` 结尾，不是 `/v1/embeddings`。

### rerank 配置

`fastQA` 和 `patentQA` 共用一套 rerank：

```env
RERANK_PROVIDER=none
RERANK_BASE_URL=
RERANK_MODEL=qwen3-vl-rerank
RERANK_API_KEY=
```

`RERANK_PROVIDER` 可选：

- `none`、`off`、`disabled`：关闭 rerank
- `local`：调用内网或本机部署的 rerank HTTP 服务
- `dashscope`：调用 DashScope rerank 服务

如果 `RERANK_PROVIDER` 不是 `none`、`off` 或 `disabled`，必须配置 `RERANK_BASE_URL`。

## 镜像构建

在仓库根目录执行：

```bash
docker build -f deploy/docker/base.Dockerfile -t highthinking-python-base:latest .
docker build -f deploy/docker/Dockerfile.gateway -t ghcr.io/example/highthinking-gateway:latest .
docker build -f deploy/docker/Dockerfile.public-service -t ghcr.io/example/highthinking-public-service:latest .
docker build -f deploy/docker/Dockerfile.fastqa -t ghcr.io/example/highthinking-fastqa:latest .
docker build -f deploy/docker/Dockerfile.highthinkingqa -t ghcr.io/example/highthinking-highthinkingqa:latest .
docker build -f deploy/docker/Dockerfile.patent -t ghcr.io/example/highthinking-patent:latest .

cd frontend-vue
npm ci
npm run build
cd ..
docker build -f deploy/docker/Dockerfile.frontend-nginx -t ghcr.io/example/highthinking-frontend:latest .
```

镜像 tag 需要和 `deploy/.env` 中的镜像变量保持一致。

## 收集向量库和运行时数据

如果需要把当前机器上的向量库、知识图谱和运行时数据一起交付，执行：

```bash
bash deploy/scripts/collect_seed_data.sh --clean
```

脚本会把数据收集到：

```text
deploy/seed-data/
```

当前默认收集范围包括：

- `public-service` 的 vector database、papers、storage、translation cache
- `fastQA` 的向量库和索引文件
- `highThinkingQA` 的 Chroma / vectordb 和 papers
- `patentQA` 的专利摘要向量库和专利原文 chunk 向量库

## 收集论文和专利原文到 MinIO seed

如果需要部署时自动把论文原文和专利原文导入 MinIO，执行：

```bash
bash deploy/scripts/collect_minio_seed.sh agentcode --clean
```

生成结果：

```text
deploy/minio-seed/agentcode/papers/
deploy/minio-seed/agentcode/patent/originals/
```

其中专利原文会按运行时需要的 MinIO 对象结构组织，例如：

```text
patent/originals/<patent_id>/manifest.json
patent/originals/<patent_id>/structured/claims.json
patent/originals/<patent_id>/structured/description.json
patent/originals/<patent_id>/structured/bibliography.json
patent/originals/<patent_id>/fulltext/original.pdf
patent/originals/<patent_id>/figures/...
```

如果只收集专利原文：

```bash
bash deploy/scripts/collect_minio_seed.sh agentcode --patent-only
```

如果只收集论文原文：

```bash
bash deploy/scripts/collect_minio_seed.sh agentcode --papers-only
```

## 部署前检查

打包或部署前执行：

```bash
bash deploy/scripts/preflight_check.sh deploy/.env
```

检查内容包括：

- 必要文件是否存在
- 必要环境变量是否存在
- `QA_EMBEDDING_MODEL_TYPE` 是否为 `remote`
- `RERANK_PROVIDER` 是否合法
- 启用 rerank 时是否配置了 `RERANK_BASE_URL`
- `seed-data` 是否为空
- `minio-seed` 是否为空
- `docker compose config` 是否能正常展开

如果 `.env` 里还有 `replace_with_real_` 或 `change_me_`，脚本会给出警告。这类警告表示仍有占位值，需要上线前替换。

## 导出镜像给离线机器

如果目标机器不能联网拉镜像，可以导出一个镜像包：

```bash
bash deploy/scripts/export_images.sh deploy/.env deploy/highthinking-images.tar
```

导出的镜像包括：

- `gateway`
- `public-service`
- `fastQA`
- `highThinkingQA`
- `patentQA`
- `frontend`
- `mysql`
- `redis`
- `minio/minio`
- `minio/mc`
- `alpine`
- `nginx`

在目标机器导入：

```bash
docker load -i deploy/highthinking-images.tar
```

## 首次启动前需要确认的文件

部署包中应包含：

- `deploy/docker-compose.yml`
- `deploy/.env`
- `deploy/mysql-init/001_schema.sql`
- `deploy/minio-init/init.sh`
- `deploy/minio-seed/<bucket>/`
- `deploy/seed-data/public-service/`
- `deploy/seed-data/fastQA/`
- `deploy/seed-data/highThinkingQA/`
- `deploy/seed-data/patentQA/`

`seed-data/` 用来初始化 Docker volume 中的向量库和运行时数据。

`minio-seed/` 用来初始化 MinIO 中的对象数据，例如论文原文和专利原文。

## 启动

在目标机器执行：

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d
```

查看容器状态：

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml ps
```

查看某个服务日志：

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml logs -f gateway
```

## 默认端口

- 前端 nginx：`8080`
- gateway：`8101`
- public-service：`8102`
- fastQA：`8008`
- highThinkingQA：`8009`
- patentQA：`8010`
- MySQL：`3306`
- Redis：`6379`
- MinIO API：`9000`
- MinIO 控制台：`9001`

## Docker 网络

默认 Docker bridge 网络：

```env
DOCKER_BRIDGE_SUBNET=172.20.0.0/24
DOCKER_BRIDGE_GATEWAY=172.20.0.1
```

如果目标机器上 Docker、VPN 或内网网段冲突，需要在首次启动前修改这两个变量。

## 首次启动行为

首次启动时：

- MySQL 会自动导入 `mysql-init/001_schema.sql`
- MinIO 会自动创建 `MINIO_BUCKET`
- `minio-seed` 会把 `deploy/minio-seed/<bucket>/` 下的数据上传到 MinIO
- `init-data` 会把 `deploy/seed-data/` 下的数据复制到 Docker named volume

注意：如果 Docker named volume 已经存在，首次初始化逻辑不会覆盖已有持久化数据。需要重新初始化时，应先确认是否可以删除旧 volume，避免误删客户数据。

## 常见注意事项

- `deploy/.env` 是最终交付时最主要的配置文件。
- API key 不一定必填，取决于客户的模型服务是否要求鉴权。
- 本地部署的模型服务也按 `remote` 模式配置，只要提供 HTTP URL。
- `resource/config` 会被打进镜像，但它只是默认配置；Docker 环境变量优先。
- MySQL 表结构只会在数据库 volume 首次创建时自动导入。
- MinIO 中的论文和专利原文通过 `minio-seed` 自动上传，不需要业务代码在启动时重新扫描本地目录。
