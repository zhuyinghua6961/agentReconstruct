# LiFeO4Agent Docker 离线部署说明

本文档给部署人员使用，目标是：拿到 `deploy` 目录后，按顺序复制命令，就能把 LiFeO4Agent 启动起来。

文档里所有命令都假设部署目录是：

```bash
/home/lifeo4agent/deploy
```

如果你的目录不一样，例如 `/opt/lifeo4agent/deploy`，把命令里的路径替换成你的实际路径即可。

## 0. 一句话说明

部署方机器上不需要提前安装 MySQL、Redis、MinIO、Neo4j，也不需要安装 `mc`、`zstd`、`neo4j-admin`。

这些都会由 Docker Compose 自动启动对应容器：

- MySQL：存用户、部门、账号、会话等业务数据。
- Redis：缓存和任务状态。
- MinIO：存文献原文、专利 PDF、专利图片、专利表格等原文对象。
- Neo4j：文献知识图谱和专利知识图谱。
- 后端服务：gateway、public-service、fastQA、highThinkingQA、patentQA。
- 前端服务：nginx 前端镜像和 HTTPS 入口 nginx。
- seed job：第一次启动时自动导入数据包。

所以部署方最少只需要准备：

- Linux 服务器。
- Docker。
- Docker Compose 插件。
- 我们交付的 `deploy` 目录。
- 大模型、embedding、rerank、intent 模型配置。
- 域名和 HTTPS 证书，或者先用自签证书测试。

## 1. 目录里应该有什么

进入部署目录：

```bash
cd /home/lifeo4agent/deploy
```

查看文件：

```bash
ls -la
```

正常应该能看到类似内容：

```text
.env
.env.example
.env.production.example
certs/
data/
docker-compose.yml
lifeo4agent-images.tar
minio-init/
mysql-init/
nginx/
scripts/
seed-tools/
```

注意：`.env` 是隐藏文件，普通 `ls` 看不到，必须用 `ls -la`。

检查数据包：

```bash
ls -lh data
```

正常应该包含：

```text
manifest.json
minio-originals.tar.zst
fastqa-ref.tar.zst
highthinking-ref.tar.zst
patentqa-ref.tar.zst
public-service-ref.tar.zst
neo4j-literature.dump.zst
neo4j-patent.dump.zst
```

这些数据包不要解压。Compose 启动时会由容器自动读取。

## 2. 服务器环境要求

最低建议：

- 操作系统：Linux x86_64，建议 Ubuntu 22.04、CentOS 7/8、Rocky Linux 8/9、Alibaba Cloud Linux 3。
- CPU：8 核以上。
- 内存：32 GB 以上，建议 64 GB。
- 磁盘：建议部署前可用空间不少于 150 GB。更稳妥是 200 GB 以上。
- Docker：20.10 或更高。
- Docker Compose：v2 插件。
- 网络：服务器能访问大模型、embedding、rerank、intent 模型的接口地址。

检查系统架构：

```bash
uname -m
```

期望看到：

```text
x86_64
```

检查磁盘：

```bash
df -hT /home/lifeo4agent
```

期望看到 `Avail` 还有足够空间，例如：

```text
Filesystem     Type  Size  Used Avail Use% Mounted on
/dev/nvme0n1p3 ext4  394G  267G  111G  71% /
```

如果 `Avail` 低于 100 GB，首次导入 MinIO 原文和向量库时可能失败，建议先扩容或清理旧数据。

检查 Docker：

```bash
docker --version
docker compose version
```

正常示例：

```text
Docker version 26.x.x, build xxxxx
Docker Compose version v2.x.x
```

如果提示 `command not found`，说明还没有安装 Docker 或 Compose 插件。

检查 Docker 是否运行：

```bash
docker info >/dev/null && echo "Docker OK"
```

正常示例：

```text
Docker OK
```

如果失败，先启动 Docker：

```bash
systemctl start docker
systemctl enable docker
```

## 3. 域名和 HTTPS

### 3.1 域名必须能解析到服务器 IP

如果部署方有内网 DNS，推荐在 DNS 里加一条 A 记录，例如域名为lifeo4.agent.test：

```text
lifeo4.agent.test  ->  服务器内网 IP
```

如果没有 DNS，可以先在访问者电脑上手动改 hosts。

Linux 或 macOS：

```bash
sudo sh -c "grep -v '[[:space:]]lifeo4.agent.test$' /etc/hosts > /tmp/hosts.lifeo4 && cat /tmp/hosts.lifeo4 > /etc/hosts && echo '服务器IP lifeo4.agent.test' >> /etc/hosts"
```

把 `服务器IP` 改成真实 IP，例如：

```bash
sudo sh -c "grep -v '[[:space:]]lifeo4.agent.test$' /etc/hosts > /tmp/hosts.lifeo4 && cat /tmp/hosts.lifeo4 > /etc/hosts && echo '172.19.14.204 lifeo4.agent.test' >> /etc/hosts"
```

macOS 再刷新 DNS 缓存：

```bash
sudo dscacheutil -flushcache
sudo killall -HUP mDNSResponder
```

Windows：

1. 用管理员身份打开记事本。
2. 打开 `C:\Windows\System32\drivers\etc\hosts`。
3. 最后一行加：

```text
服务器IP lifeo4.agent.test
```

例如：

```text
172.19.14.204 lifeo4.agent.test
```

只要只添加这一行，就只影响这个域名，不会影响正常上网。

测试域名解析：

```bash
ping lifeo4.agent.test
```

如果能看到解析到服务器 IP，说明域名解析生效。即使 ping 不通，也可能只是服务器禁了 ICMP；只要浏览器能打开 HTTPS，就不影响系统使用。

## 4. HTTPS 证书怎么准备

证书文件放在：

```bash
/home/lifeo4agent/deploy/certs
```

必须有两个文件：

```text
certs/fullchain.pem
certs/privkey.pem
```

含义：

- `fullchain.pem`：服务器证书，或者证书链。
- `privkey.pem`：服务器私钥。

### 4.1 正式部署推荐方式：使用部署方自己的证书

如果部署方自己签发证书，或者内网 CA 签发证书，让他们提供：

```text
fullchain.pem
privkey.pem
```

替换到目录：

```bash
cd /home/lifeo4agent/deploy
cp /路径/部署方证书.pem certs/fullchain.pem
cp /路径/部署方私钥.key certs/privkey.pem
chmod 644 certs/fullchain.pem
chmod 600 certs/privkey.pem
```

检查证书域名：

```bash
openssl x509 -in certs/fullchain.pem -noout -subject -issuer -dates -ext subjectAltName
```

重点看 `DNS:` 后面是否包含 `.env` 里的 `HTTPS_SERVER_NAME`。

例如 `.env` 里写：

```bash
HTTPS_SERVER_NAME=lifeo4.agent.test
```

那证书里必须包含：

```text
DNS:lifeo4.agent.test
```

如果证书不包含这个域名，浏览器会报证书域名不匹配。

### 4.2 测试部署方式：使用自签名证书

如果只是内网测试，可以用自签名证书。

在有源码和脚本的机器上执行：

```bash
cd /home/lifeo4agent/deploy
bash scripts/generate_dev_tls_cert.sh lifeo4.agent.test 172.19.14.204
```

把 `lifeo4.agent.test` 改成测试域名，把 `172.19.14.204` 改成服务器 IP。

成功时会输出类似：

```text
subject=C = CN, O = LiFeO4Agent, CN = lifeo4.agent.test
issuer=C = CN, O = LiFeO4Agent, CN = LiFeO4Agent Internal Test Root CA
X509v3 Subject Alternative Name:
    DNS:lifeo4.agent.test, DNS:localhost, IP Address:172.19.14.204, IP Address:127.0.0.1
```

脚本会生成：

```text
certs/fullchain.pem
certs/privkey.pem
certs/rootCA.pem
certs/rootCA.key
```

部署时真正需要：

```text
certs/fullchain.pem
certs/privkey.pem
```

如果希望浏览器不提示“不安全”，还需要把 `certs/rootCA.pem` 导入访问者电脑的受信任根证书里。

注意：

- `rootCA.key` 是根证书私钥，不建议发给客户或普通用户。
- 自签名证书适合测试，不建议作为正式生产证书。
- 正式环境最好由部署方内网 CA 或正式 CA 签发证书。

### 4.3 如果没有生成脚本，也可以手工生成测试证书

在部署目录执行：

```bash
cd /home/lifeo4agent/deploy
mkdir -p certs
```

创建 OpenSSL 配置：

```bash
cat > /tmp/lifeo4agent-openssl.cnf <<'EOF'
[req]
default_bits = 2048
prompt = no
default_md = sha256
distinguished_name = dn
x509_extensions = v3_req

[dn]
C = CN
O = LiFeO4Agent
CN = lifeo4.agent.test

[v3_req]
subjectAltName = @alt_names

[alt_names]
DNS.1 = lifeo4.agent.test
DNS.2 = localhost
IP.1 = 172.19.14.204
IP.2 = 127.0.0.1
EOF
```

把里面的 `lifeo4.agent.test` 和 `172.19.14.204` 改成真实域名和 IP。

生成证书：

```bash
openssl req -x509 -nodes -days 825 \
  -newkey rsa:2048 \
  -keyout certs/privkey.pem \
  -out certs/fullchain.pem \
  -config /tmp/lifeo4agent-openssl.cnf
```

设置权限：

```bash
chmod 600 certs/privkey.pem
chmod 644 certs/fullchain.pem
```

检查证书：

```bash
openssl x509 -in certs/fullchain.pem -noout -subject -dates -ext subjectAltName
```

## 5. 配置文件在哪里

主配置文件是：

```bash
/home/lifeo4agent/deploy/.env
```

这是隐藏文件。查看：

```bash
cd /home/lifeo4agent/deploy
ls -la .env
```

编辑：

```bash
vim .env
```

如果没有 `.env`，从模板复制：

```bash
cp .env.production.example .env
vim .env
```

重要原则：

- 第一次启动前一定要把必须修改的配置改好。
- MySQL、Redis、MinIO 密码一旦初始化进 volume，启动后再改 `.env` 不一定会同步修改已有数据。
- 如果只是改大模型、embedding、rerank、intent 配置，通常改完 `.env` 后重启后端即可。
- 不要把 `.env` 发到公开群或公网仓库，因为里面会有密钥。

## 6. `.env` 必须修改的配置

下面这些是正式部署前必须确认的配置。

### 6.1 对外访问域名和端口

```bash
HTTP_PUBLISH_PORT=80
HTTPS_PUBLISH_PORT=443
HTTPS_SERVER_NAME=你的域名
HTTPS_REDIRECT_HOST=你的域名
```

含义：

- `HTTP_PUBLISH_PORT`：HTTP 入口端口，只用于自动跳转到 HTTPS。
- `HTTPS_PUBLISH_PORT`：HTTPS 入口端口，用户主要访问它。
- `HTTPS_SERVER_NAME`：证书里的域名。
- `HTTPS_REDIRECT_HOST`：HTTP 跳 HTTPS 时跳到哪里。

标准 443 端口示例：

```bash
HTTP_PUBLISH_PORT=80
HTTPS_PUBLISH_PORT=443
HTTPS_SERVER_NAME=lifeo4.agent.test
HTTPS_REDIRECT_HOST=lifeo4.agent.test
```

非标准端口示例：

```bash
HTTP_PUBLISH_PORT=18081
HTTPS_PUBLISH_PORT=18443
HTTPS_SERVER_NAME=lifeo4.agent.test
HTTPS_REDIRECT_HOST=lifeo4.agent.test:18443
```

非标准端口访问地址就是：

```text
https://lifeo4.agent.test:18443
```

### 6.2 MySQL、Redis、MinIO 密码

```bash
MYSQL_ROOT_PASSWORD=请改成强密码
MYSQL_APP_USER=agentcode
MYSQL_APP_PASSWORD=请改成强密码

REDIS_PASSWORD=请改成强密码

MINIO_ROOT_USER=admin
MINIO_ROOT_PASSWORD=请改成强密码
MINIO_BUCKET=agentcode
```

说明：

- 部署机不需要预装 MySQL、Redis、MinIO。
- 这些密码是容器内部使用的，也是后端连接这些容器的密码。
- `MYSQL_APP_USER` 可以不改，默认 `agentcode`。
- `MINIO_BUCKET` 可以不改，默认 `agentcode`。
- `MINIO_ROOT_PASSWORD` 建议至少 8 位以上。

生成随机密码示例：

```bash
openssl rand -base64 24
```

注意：如果已经启动过，并且 MySQL/Redis/MinIO volume 已经初始化，再改这些密码可能不会自动更新已有数据库。最稳妥是在第一次启动前改好。

### 6.3 JWT 和内部服务令牌

```bash
JWT_SECRET=请改成随机长字符串
PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN=请改成随机长字符串
```

生成方式：

```bash
openssl rand -hex 32
```

分别生成两次，填两个不同的值。

说明：

- `JWT_SECRET` 用来签发登录态 token。
- `PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN` 用来保护内部服务间调用。
- 这两个值不要用简单密码，不要公开。

### 6.4 LLM 大模型配置

```bash
LLM_API_KEY=你的大模型Key
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=deepseek-v3.1
```

说明：

- `LLM_BASE_URL` 通常填 OpenAI 兼容接口的 base URL，不要填到 `/chat/completions`。
- 如果是 DashScope OpenAI 兼容模式，常见写法是：

```bash
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

- 如果是本地 OpenAI 兼容服务，例如部署在宿主机 8000 端口：

```bash
LLM_BASE_URL=http://host.docker.internal:8000/v1
LLM_API_KEY=本地服务如果不校验Key可以留空或填dummy
LLM_MODEL=你的模型名
```

`host.docker.internal` 在本 compose 里已经配置过，容器可以用它访问宿主机服务。

### 6.5 fastQA 和 patentQA 的 embedding 配置

```bash
QA_EMBEDDING_API_KEY=
QA_EMBEDDING_BASE_URL=http://host.docker.internal:8001/v1/embeddings
QA_EMBEDDING_MODEL=bge-local
```

说明：

- fastQA 和 patentQA 共用这组 embedding 配置。
- `QA_EMBEDDING_BASE_URL` 当前按完整 embeddings 接口填写，常见形式是：

```text
http://服务地址:端口/v1/embeddings
```

如果 embedding 服务部署在宿主机：

```bash
QA_EMBEDDING_BASE_URL=http://host.docker.internal:8001/v1/embeddings
```

如果 embedding 服务部署在另一台机器：

```bash
QA_EMBEDDING_BASE_URL=http://192.168.1.20:8001/v1/embeddings
```

如果接口需要 key，就填：

```bash
QA_EMBEDDING_API_KEY=你的EmbeddingKey
```

如果本地服务不需要 key，可以留空。

### 6.6 highThinkingQA 的 embedding 配置

```bash
HIGHTHINKINGQA_EMBEDDING_API_KEY=你的EmbeddingKey
HIGHTHINKINGQA_EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
HIGHTHINKINGQA_EMBEDDING_MODEL=text-embedding-v4
```

说明：

- highThinkingQA 使用独立 embedding 配置，不和 fastQA/patentQA 共用。
- 这里的 base URL 通常是 OpenAI 兼容 base URL，例如 `/v1`，不是完整 `/embeddings` 路径。
- 如果使用本地 embedding 服务，根据服务实现填写对应 base URL 和模型名。

### 6.6.1 highThinkingQA 的 tiktoken 离线缓存

highThinkingQA 会用 tiktoken 的 `cl100k_base` 编码估算 token 数。离线或内网环境如果没有缓存，会尝试访问：

```text
https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken
```

部署编排会把缓存目录固定到容器内：

```text
/data/highthinkingqa/runtime/tiktoken-cache
```

如果现场不能访问该地址，需要把 `cl100k_base.tiktoken` 预先放入这个目录，文件名为：

```text
9b5ad71b2ce5302211f9c61530b329a4922fc6a4
```

### 6.7 rerank 配置

```bash
RERANK_PROVIDER=local
RERANK_BASE_URL=http://host.docker.internal:8084
RERANK_MODEL=qwen3-vl-rerank
RERANK_API_KEY=
```

说明：

- fastQA 和 patentQA 共用这组 rerank 配置。
- 如果没有 rerank 服务，先关闭：

```bash
RERANK_PROVIDER=none
RERANK_BASE_URL=
RERANK_MODEL=qwen3-vl-rerank
RERANK_API_KEY=
```

- 如果 rerank 服务在宿主机：

```bash
RERANK_PROVIDER=local
RERANK_BASE_URL=http://host.docker.internal:8084
```

- 如果 rerank 服务在其他机器：

```bash
RERANK_PROVIDER=local
RERANK_BASE_URL=http://192.168.1.20:8084
```

### 6.8 intent 意图识别模型配置

```bash
INTENT_MODEL_ENABLED=true
INTENT_MODEL_API_KEY=你的Intent模型Key
INTENT_MODEL_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
INTENT_MODEL=qwen3-8b
INTENT_MODEL_TIMEOUT_SECONDS=30
```

说明：

- fastQA 和 patentQA 共用 intent 模型配置。
- 如果不想启用 intent，写：

```bash
INTENT_MODEL_ENABLED=false
INTENT_MODEL_API_KEY=
```

- `INTENT_MODEL_BASE_URL` 也是 OpenAI 兼容 base URL，不要写到 `/chat/completions`。
- 如果 intent 用同一个 LLM Key，可以把 `INTENT_MODEL_API_KEY` 填成和 `LLM_API_KEY` 一样。

### 6.9 数据包版本

```bash
DATA_PACKAGE_VERSION=2026-05-19
DATA_SEED_FORCE=0
```

说明：

- `DATA_PACKAGE_VERSION` 必须和 `data/manifest.json` 里的 `data_version` 一致。
- 当前交付数据包版本是 `2026-05-19`。
- `DATA_SEED_FORCE=0` 表示同版本已经导入过就跳过。
- `DATA_SEED_FORCE=1` 表示强制重新导入数据。平时不要开，只有需要重灌数据时再开。

检查数据包版本：

```bash
grep '"data_version"' data/manifest.json
```

正常示例：

```text
"data_version": "2026-05-19",
```

## 7. `.env` 可以不改的配置

下面这些通常不用改。

### 7.1 前端内部端口

```bash
FRONTEND_PUBLISH_PORT=8080
```

说明：

- 真正给用户访问的是 `HTTPS_PUBLISH_PORT`。
- `FRONTEND_PUBLISH_PORT` 默认只绑定 `127.0.0.1`，主要给 edge nginx 内部使用。
- 不知道用途时不用改。

### 7.2 MySQL、Redis、MinIO 对外端口

```bash
MYSQL_PUBLISH_PORT=3306
REDIS_PUBLISH_PORT=6379
MINIO_API_PUBLISH_PORT=9000
MINIO_CONSOLE_PUBLISH_PORT=9001
```

如果服务器已有 MySQL、Redis、MinIO 占用这些端口，就改成别的，例如：

```bash
MYSQL_PUBLISH_PORT=13306
REDIS_PUBLISH_PORT=16379
MINIO_API_PUBLISH_PORT=19001
MINIO_CONSOLE_PUBLISH_PORT=19002
```

说明：

- 改这些端口只影响宿主机访问容器的端口。
- 容器内部互相连接仍然走服务名和内部端口，不需要改后端代码。
- 如果部署方不希望外部访问 MySQL/Redis/MinIO，可以用防火墙限制访问。

### 7.3 镜像名

默认镜像名：

```bash
GATEWAY_IMAGE=lifeo4agent/gateway:latest
PUBLIC_SERVICE_IMAGE=lifeo4agent/public-service:latest
FASTQA_IMAGE=lifeo4agent/fastqa:latest
HIGHTHINKINGQA_IMAGE=lifeo4agent/highthinkingqa:latest
PATENT_IMAGE=lifeo4agent/patent:latest
FRONTEND_IMAGE=lifeo4agent/frontend:latest
SEED_TOOLS_IMAGE=lifeo4agent/seed-tools:latest
NEO4J_IMAGE_TAG=5.26.12
```

如果 `docker-compose.yml` 使用默认值，并且 `lifeo4agent-images.tar` 是本次交付镜像包，就不用改镜像名。

只有我们后续交付了新 tag，例如：

```bash
FASTQA_IMAGE=lifeo4agent/fastqa:2026-05-21
```

才需要在 `.env` 里覆盖。

### 7.4 Docker 网桥

```bash
DOCKER_BRIDGE_SUBNET=172.20.0.0/24
DOCKER_BRIDGE_GATEWAY=172.20.0.1
```

默认可以不写。

只有当部署机已有 Docker 网络和 `172.20.0.0/24` 冲突时，才改成别的网段。

## 8. 第一次部署完整命令

以下命令只要按顺序执行即可。

### 8.1 进入部署目录

```bash
cd /home/lifeo4agent/deploy
pwd
```

期望输出：

```text
/home/lifeo4agent/deploy
```

### 8.2 确认 `.env` 存在

```bash
ls -la .env
```

期望输出类似：

```text
-rw-r--r-- 1 root root 4000 May 21 12:00 .env
```

如果不存在：

```bash
cp .env.production.example .env
vim .env
```

### 8.3 加载 Docker 镜像

```bash
docker load -i lifeo4agent-images.tar
```

正常会输出很多行，例如：

```text
Loaded image: lifeo4agent/gateway:latest
Loaded image: lifeo4agent/public-service:latest
Loaded image: lifeo4agent/fastqa:latest
Loaded image: lifeo4agent/highthinkingqa:latest
Loaded image: lifeo4agent/patent:latest
Loaded image: lifeo4agent/frontend:latest
Loaded image: lifeo4agent/seed-tools:latest
Loaded image: mysql:8.0
Loaded image: redis:7
Loaded image: minio/minio:latest
Loaded image: minio/mc:latest
Loaded image: neo4j:5.26.12
Loaded image: nginx:1.27-alpine
```

检查镜像：

```bash
docker images | grep -E 'lifeo4agent|mysql|redis|minio|neo4j|nginx'
```

能看到相关镜像即可。

### 8.4 预检查配置和数据包

```bash
bash scripts/preflight_check.sh .env
```

正常会输出类似：

```text
ok: data package manifest and sha256 validated with python
ok: docker image present: lifeo4agent/gateway:latest
ok: docker image present: lifeo4agent/public-service:latest
ok: docker image present: lifeo4agent/fastqa:latest
ok: docker image present: lifeo4agent/highthinkingqa:latest
ok: docker image present: lifeo4agent/patent:latest
ok: docker image present: lifeo4agent/frontend:latest
preflight check passed
```

如果看到：

```text
missing required variable
```

说明 `.env` 里有必填项为空。

如果看到：

```text
missing required data package
```

说明 `data/` 目录缺数据包。

如果看到：

```text
sha256 mismatch
```

说明文件传输不完整或损坏，需要重新传对应数据包。

### 8.5 看 compose 展开是否正常

```bash
docker compose --env-file .env -f docker-compose.yml config --images
```

正常会输出一组镜像名，例如：

```text
mysql:8.0
redis:7
minio/minio:latest
minio/mc:latest
lifeo4agent/seed-tools:latest
neo4j:5.26.12
lifeo4agent/public-service:latest
lifeo4agent/fastqa:latest
lifeo4agent/highthinkingqa:latest
lifeo4agent/patent:latest
lifeo4agent/gateway:latest
lifeo4agent/frontend:latest
nginx:1.27-alpine
```

### 8.6 启动系统

```bash
docker compose --env-file .env -f docker-compose.yml up -d
```

第一次启动会比较久，因为要导入：

- MinIO 原文包，约 50 GB 压缩包。
- fastQA 向量库。
- highThinkingQA 向量库。
- patentQA 向量库和 JSON archive。
- public-service 轻量向量库。
- 文献 Neo4j dump。
- 专利 Neo4j dump。

正常输出类似：

```text
Container ... Started
Container ... Healthy
Container ... Exited
```

其中 seed 容器正常完成后会显示 `Exited`，这不是错误。seed 容器是一次性任务，导入完就退出。

### 8.7 查看启动状态

```bash
docker compose --env-file .env -f docker-compose.yml ps
```

期望看到：

- `mysql`、`redis`、`minio`、`neo4j-literature`、`neo4j-patent` 是 running 或 healthy。
- `public-service`、`fastqa`、`highthinkingqa`、`patent`、`gateway`、`frontend`、`edge` 是 running。
- `*-seed`、`*-prepare` 是 exited 0 或 completed。

如果某个容器一直 `Restarting` 或 `Exited 1`，看日志。

## 9. 访问系统

如果 `.env` 是标准 443：

```text
https://你的域名
```

如果 `.env` 是非标准端口，例如：

```bash
HTTPS_PUBLISH_PORT=18443
HTTPS_REDIRECT_HOST=lifeo4.agent.test:18443
```

访问：

```text
https://lifeo4.agent.test:18443
```

如果用自签名证书，浏览器可能提示“不安全”。测试时可以继续访问；正式环境应导入根证书或使用正式证书。

默认管理员账号：

```text
用户名：admin
密码：whyxadmin123..
```

第一次登录后建议立即修改密码，并按页面提示设置安全问题。

## 10. 查看日志

查看所有服务日志：

```bash
docker compose --env-file .env -f docker-compose.yml logs -f
```

查看最近 200 行：

```bash
docker compose --env-file .env -f docker-compose.yml logs --tail=200
```

查看某个服务：

```bash
docker compose --env-file .env -f docker-compose.yml logs -f gateway
docker compose --env-file .env -f docker-compose.yml logs -f public-service
docker compose --env-file .env -f docker-compose.yml logs -f fastqa
docker compose --env-file .env -f docker-compose.yml logs -f highthinkingqa
docker compose --env-file .env -f docker-compose.yml logs -f patent
docker compose --env-file .env -f docker-compose.yml logs -f edge
```

查看数据导入日志：

```bash
docker compose --env-file .env -f docker-compose.yml logs minio-seed
docker compose --env-file .env -f docker-compose.yml logs fastqa-ref-seed
docker compose --env-file .env -f docker-compose.yml logs highthinking-ref-seed
docker compose --env-file .env -f docker-compose.yml logs patentqa-ref-seed
docker compose --env-file .env -f docker-compose.yml logs neo4j-literature-seed
docker compose --env-file .env -f docker-compose.yml logs neo4j-patent-seed
```

如果日志里看到：

```text
version already imported
```

表示同版本数据已经导入过，本次自动跳过，是正常情况。

## 11. 停止、重启、更新

停止系统，但保留数据：

```bash
docker compose --env-file .env -f docker-compose.yml down
```

重新启动：

```bash
docker compose --env-file .env -f docker-compose.yml up -d
```

修改 `.env` 后重启：

```bash
docker compose --env-file .env -f docker-compose.yml up -d
```

只重启某个服务，例如 fastQA：

```bash
docker compose --env-file .env -f docker-compose.yml restart fastqa
```

更新镜像包：

```bash
docker load -i lifeo4agent-images.tar
docker compose --env-file .env -f docker-compose.yml up -d
```

如果只更新了 fastQA 镜像：

```bash
docker load -i lifeo4agent-fastqa.tar
docker compose --env-file .env -f docker-compose.yml up -d fastqa
```

注意：不要随便执行 `down -v`，它会删除数据库、MinIO、向量库、Neo4j 等 Docker volume。

## 12. 强制重新导入数据

默认：

```bash
DATA_SEED_FORCE=0
```

同版本数据已经导入过时，seed job 会跳过。

如果确实要重新导入同一版本数据，改成：

```bash
DATA_SEED_FORCE=1
```

然后执行：

```bash
docker compose --env-file .env -f docker-compose.yml up -d
```

导入完成后，建议改回：

```bash
DATA_SEED_FORCE=0
```

不要长期保持 `DATA_SEED_FORCE=1`，否则每次启动都可能重复导入，耗时很长。

## 13. 完全重装

这一步会删除所有容器数据，包括：

- MySQL 用户和业务数据。
- Redis 数据。
- MinIO 原文对象。
- Neo4j 图谱数据。
- 各服务 reference data volume。

只有在测试环境或确认要重装时才执行。

```bash
cd /home/lifeo4agent/deploy
docker compose --env-file .env -f docker-compose.yml down -v
docker compose --env-file .env -f docker-compose.yml up -d
```

如果只是想停服务，不要加 `-v`。

## 14. 常见问题

### 14.1 看不到 `.env`

`.env` 是隐藏文件。

用：

```bash
ls -la
```

不要用普通 `ls`。

### 14.2 端口被占用

错误类似：

```text
bind: address already in use
```

查看端口：

```bash
ss -lntp | grep -E ':80|:443|:3306|:6379|:9000|:9001'
```

解决方法：

1. 停掉占用端口的旧服务。
2. 或者修改 `.env` 里的端口，例如把 443 改成 18443。

如果 HTTPS 端口改成 18443，记得：

```bash
HTTPS_PUBLISH_PORT=18443
HTTPS_REDIRECT_HOST=你的域名:18443
```

### 14.3 MySQL 或 Redis 密码改了但服务连不上

通常原因是：系统已经启动过，Docker volume 里已经初始化了旧密码。

解决方法：

- 测试环境：可以 `docker compose down -v` 后重新初始化。
- 正式环境：不要直接删 volume，需要手工进 MySQL/Redis 改密码，或联系交付方处理。

所以密码最好第一次启动前就改好。

### 14.4 大模型 401 或 403

通常是 key 不对。

检查：

```bash
grep -E '^LLM_API_KEY=|^LLM_BASE_URL=|^LLM_MODEL=' .env
```

确认：

- `LLM_API_KEY` 是有效 key。
- `LLM_BASE_URL` 是 base URL，不是完整 `/chat/completions`。
- `LLM_MODEL` 是服务端支持的模型名。

改完后重启后端：

```bash
docker compose --env-file .env -f docker-compose.yml restart public-service fastqa highthinkingqa patent
```

### 14.5 embedding 调用失败

检查 fastQA/patentQA embedding：

```bash
grep -E '^QA_EMBEDDING_' .env
```

检查 highThinkingQA embedding：

```bash
grep -E '^HIGHTHINKINGQA_EMBEDDING_' .env
```

如果 embedding 服务在宿主机，容器内应该使用：

```text
host.docker.internal
```

例如：

```bash
QA_EMBEDDING_BASE_URL=http://host.docker.internal:8001/v1/embeddings
```

如果 embedding 服务在其他服务器，使用那台服务器的 IP。

### 14.6 rerank 不通

如果没有 rerank 服务，先关闭：

```bash
RERANK_PROVIDER=none
RERANK_BASE_URL=
```

如果启用 rerank，就必须保证：

```bash
RERANK_PROVIDER=local
RERANK_BASE_URL=http://真实地址:端口
```

改完重启：

```bash
docker compose --env-file .env -f docker-compose.yml restart fastqa patent
```

### 14.7 intent 模型报错

如果 intent 暂时不可用，可以关闭：

```bash
INTENT_MODEL_ENABLED=false
INTENT_MODEL_API_KEY=
```

改完重启：

```bash
docker compose --env-file .env -f docker-compose.yml restart fastqa patent
```

如果要启用，确认：

```bash
INTENT_MODEL_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
INTENT_MODEL=qwen3-8b
INTENT_MODEL_API_KEY=有效Key
```

### 14.8 浏览器提示证书不安全

如果使用自签名证书，这是正常现象。

解决方式：

- 测试环境：浏览器选择继续访问。
- 内网正式环境：把 `certs/rootCA.pem` 导入客户端信任根证书。
- 正式生产环境：换成部署方内网 CA 或正式 CA 签发的证书。

### 14.9 数据导入很慢

第一次启动会导入 50 GB 级别的 MinIO 原文包，这是正常的。

查看进度：

```bash
docker compose --env-file .env -f docker-compose.yml logs -f minio-seed
```

不要在导入过程中反复 `down -v`，否则会重新开始。

### 14.10 机器上已经有 MySQL、Redis、MinIO，会不会覆盖

不会直接覆盖宿主机已有的数据。

原因：

- 本系统启动的是 Docker 容器。
- 数据存到 Docker named volume。
- 宿主机已有 MySQL/Redis/MinIO 数据目录不会被 compose 直接使用。

但如果端口相同，会端口冲突。解决办法是改 `.env` 里的发布端口：

```bash
MYSQL_PUBLISH_PORT=13306
REDIS_PUBLISH_PORT=16379
MINIO_API_PUBLISH_PORT=19001
MINIO_CONSOLE_PUBLISH_PORT=19002
```

## 15. 最短部署命令清单

如果配置和证书已经准备好，最短命令就是：

```bash
cd /home/lifeo4agent/deploy
docker load -i lifeo4agent-images.tar
bash scripts/preflight_check.sh .env
docker compose --env-file .env -f docker-compose.yml up -d
docker compose --env-file .env -f docker-compose.yml ps
```

访问：

```text
https://你的域名
```

默认管理员：

```text
admin / whyxadmin123..
```

## 16. 给部署方的最终检查表

部署前逐项确认：

- [ ] `docker --version` 正常。
- [ ] `docker compose version` 正常。
- [ ] `/home/lifeo4agent/deploy/.env` 存在。
- [ ] `certs/fullchain.pem` 存在。
- [ ] `certs/privkey.pem` 存在。
- [ ] 证书域名和 `HTTPS_SERVER_NAME` 一致。
- [ ] 域名能解析到服务器 IP。
- [ ] `lifeo4agent-images.tar` 存在。
- [ ] `data/manifest.json` 存在。
- [ ] 7 个数据包都在 `data/` 目录。
- [ ] `.env` 中密码、JWT、内部 token 已确认。
- [ ] `.env` 中 LLM、embedding、rerank、intent 配置已确认。
- [ ] 端口没有冲突。
- [ ] `bash scripts/preflight_check.sh .env` 通过。

部署后逐项确认：

- [ ] `docker compose ps` 里核心服务都是 running/healthy。
- [ ] seed job 是 exited 0 或 completed。
- [ ] 浏览器能打开 HTTPS 页面。
- [ ] 管理员账号能登录。
- [ ] 文献问答能发起请求。
- [ ] 深度问答能发起请求。
- [ ] 专利问答能发起请求。
- [ ] MinIO 控制台可以按配置端口访问。
