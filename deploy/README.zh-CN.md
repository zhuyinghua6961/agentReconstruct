# Docker 离线部署说明

## 目标

`deploy/` 是最终交付用的 Docker 部署包，覆盖 gateway、四个后端服务、
前端 nginx、MySQL、Redis、MinIO、两个 Neo4j 图谱库，以及自动导入数据
的 one-shot seed job。

交付形态采用混合方案：

- 运行镜像：业务服务、前端、MySQL、Redis、MinIO、Neo4j、`minio/mc`、
  `lifeo4agent/seed-tools`。
- 版本化数据包：`deploy/data/*.tar.zst`。
- `docker compose up -d` 时自动把 MinIO 原文、reference 向量数据和 Neo4j
  dump 导入 Docker named volume。
- MySQL 初始化会创建 schema、导入“电池材料技术研究中心”这套部门基础数据，
  并创建一个初始 `admin` 管理员；人员、会话和配额使用记录不进入交付 seed。

部署机不需要安装 `mc`、`zstd` 或 `neo4j-admin`。

## 客户配置面

从模板生成实际配置：

```bash
cp deploy/.env.production.example deploy/.env
```

客户主要只需要改：

- HTTPS/HTTP 入口端口、前端调试端口、MySQL、Redis、MinIO 的宿主机端口
- `HTTPS_SERVER_NAME` 和 `HTTPS_REDIRECT_HOST`
- MySQL、Redis、MinIO 账号密码
- `DATA_PACKAGE_VERSION` 和 `DATA_SEED_FORCE`
- `JWT_SECRET` 和 `PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN`
- LLM endpoint、model、key
- fastQA/patentQA intent model endpoint、model、key
- fastQA/patentQA embedding endpoint、model、key
- highThinkingQA embedding endpoint、model、key
- rerank provider、endpoint、model、key

Neo4j 由 Compose 内置为 `neo4j-literature` 和 `neo4j-patent`，不再暴露给
客户配置。

## HTTPS 入口

Compose 内置 `edge` nginx 作为最外层 HTTPS 入口：

- `HTTP_PUBLISH_PORT` 会跳转到 HTTPS。
- `HTTPS_PUBLISH_PORT` 反代到内部 `frontend:80`。
- `frontend` 自身的 `FRONTEND_PUBLISH_PORT` 默认只绑定 `127.0.0.1`，
  作为本机调试入口，正式访问走 HTTPS edge。

部署方需要把自己的证书放到：

```text
deploy/certs/fullchain.pem
deploy/certs/privkey.pem
```

证书中的域名必须和 `HTTPS_SERVER_NAME` 一致；`HTTPS_REDIRECT_HOST` 用于
HTTP 跳转，标准 443 端口通常填写同一个域名，非标准端口可写成
`domain:port`。

本地测试可生成自签测试证书：

```bash
bash deploy/scripts/generate_dev_tls_cert.sh lifeo4.agent.test 172.19.14.204
```

然后在测试机 hosts 或内网 DNS 中把 `lifeo4.agent.test` 指向部署机 IP。

## 构建镜像

在仓库根目录执行：

```bash
docker build -f deploy/docker/base.Dockerfile -t lifeo4agent/python-base:latest .
docker build -f deploy/docker/Dockerfile.seed-tools -t lifeo4agent/seed-tools:latest .
docker build -f deploy/docker/Dockerfile.gateway -t lifeo4agent/gateway:latest .
docker build -f deploy/docker/Dockerfile.public-service -t lifeo4agent/public-service:latest .
docker build -f deploy/docker/Dockerfile.fastqa -t lifeo4agent/fastqa:latest .
docker build -f deploy/docker/Dockerfile.highthinkingqa -t lifeo4agent/highthinkingqa:latest .
docker build -f deploy/docker/Dockerfile.patent -t lifeo4agent/patent:latest .

cd frontend-vue && npm ci && npm run build
cd ..
docker build -f deploy/docker/Dockerfile.frontend-nginx -t lifeo4agent/frontend:latest .
```

Python base 镜像只放依赖。各服务镜像只复制本服务代码，以及共享的
`resource/config`、`resource/assets`，不再把整个 repo 或大数据塞进镜像。

## 构建数据包

先从本地 `resource/` 收集 MinIO 原文 seed：

```bash
bash deploy/scripts/collect_minio_seed.sh agentcode --clean
```

生成：

- `deploy/minio-seed/agentcode/papers/`
- `deploy/minio-seed/agentcode/patent/originals/`

专利 `*_tables.json` 会回填为
`patent/originals/<id>/structured/tables.json`，并同步更新对应
`manifest.json`。

然后在图谱维护窗口生成一致性 Neo4j dump，并统一打包：

```bash
NEO4J_LITERATURE_DUMP_SRC=/path/to/literature.dump \
NEO4J_PATENT_DUMP_SRC=/path/to/patent.dump \
bash deploy/scripts/package_data.sh deploy/.env
```

`deploy/data/` 应包含：

- `manifest.json`
- `minio-originals.tar.zst`：论文和专利原文，不包含 bucket 名
- `fastqa-ref.tar.zst`：fastQA 向量库、md 向量库、topic index，不含论文原文
- `highthinking-ref.tar.zst`：highThinkingQA `vectordb`，papers 只保留空缓存目录
- `patentqa-ref.tar.zst`：专利两个向量库和 JSON-only archive，不含 PDF/PNG
- `public-service-ref.tar.zst`：public-service 必要 reference vector data
- `neo4j-literature.dump.zst`
- `neo4j-patent.dump.zst`

`manifest.json` 记录版本、sha256、文件大小、构建时间和关键计数。

## 导出镜像

```bash
bash deploy/scripts/export_images.sh deploy/.env deploy/lifeo4agent-images.tar
```

这个 tar 只包含运行镜像和基础组件镜像。几十 GB 的数据包继续放在
`deploy/data/`，不混入镜像 tar。

旧的 MinIO 原文大镜像脚本保留为 legacy/debug 路径，不再是推荐交付流程。

## 交付前检查

```bash
bash deploy/scripts/preflight_check.sh deploy/.env
```

检查内容包括 env 必填项、数据包是否齐全、manifest sha256、Docker 镜像
是否存在，以及 Compose 是否能正常展开。

## 部署启动

目标机器执行：

```bash
docker load -i deploy/lifeo4agent-images.tar
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d
```

seed job 会写 marker：

- MinIO：`_deploy/data-seed/<package>/<version>.done`
- reference volume：`.deploy/data-seed/<package>/<version>.done`
- Neo4j volume：`/data/.deploy/data-seed/<package>/<version>.done`

同版本再次启动会跳过；需要强制重导时设置 `DATA_SEED_FORCE=1`。
