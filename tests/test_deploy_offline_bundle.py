from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _service_block(compose: str, service_name: str) -> str:
    marker = f"  {service_name}:\n"
    start = compose.index(marker)
    match = re.search(r"\n  [A-Za-z0-9_-]+:\n", compose[start + len(marker) :])
    next_start = -1 if match is None else start + len(marker) + match.start()
    return compose[start:] if next_start == -1 else compose[start:next_start]


def _has_env_mapping(block: str, key: str, value: str) -> bool:
    return re.search(rf"^\s+{re.escape(key)}:\s+{re.escape(value)}$", block, re.MULTILINE) is not None


def _require_openssl() -> None:
    if shutil.which("openssl") is None:
        pytest.skip("openssl is required for TLS certificate script tests")


def test_offline_compose_includes_patent_and_frontend_services() -> None:
    content = _read(DEPLOY / "docker-compose.yml")

    assert "  patent:" in content
    assert "image: ${PATENT_IMAGE:-lifeo4agent/patent:latest}" in content
    assert "PATENT_PORT: 8010" in content
    assert "PATENT_AUTHORITY_BASE_URL: http://public-service:8102" in content
    assert "PATENT_NEO4J_URL: bolt://neo4j-patent:7687" in content
    assert "- patentqa_ref_data:/app/resource/patentQA" in content

    assert "  frontend:" in content
    assert "image: ${FRONTEND_IMAGE:-lifeo4agent/frontend:latest}" in content
    assert '"${FRONTEND_BIND_ADDRESS:-127.0.0.1}:${FRONTEND_PUBLISH_PORT:-8080}:80"' in content
    assert "gateway:" in content


def test_offline_compose_includes_https_edge_service() -> None:
    compose = _read(DEPLOY / "docker-compose.yml")
    edge_template = _read(DEPLOY / "nginx" / "edge-https.conf.template")
    env_template = _read(DEPLOY / ".env.production.example")

    assert "  edge:" in compose
    assert "image: nginx:${NGINX_IMAGE_TAG:-1.27-alpine}" in compose
    assert '"${HTTP_PUBLISH_PORT:-80}:80"' in compose
    assert '"${HTTPS_PUBLISH_PORT:-443}:443"' in compose
    assert "./nginx/edge-https.conf.template:/etc/nginx/templates/default.conf.template:ro" in compose
    assert "./certs:/etc/nginx/certs:ro" in compose
    assert "HTTPS_SERVER_NAME: ${HTTPS_SERVER_NAME:-lifeo4.agent.test}" in compose
    assert "HTTPS_REDIRECT_HOST: ${HTTPS_REDIRECT_HOST:-lifeo4.agent.test}" in compose

    assert "ssl_certificate /etc/nginx/certs/fullchain.pem;" in edge_template
    assert "ssl_certificate_key /etc/nginx/certs/privkey.pem;" in edge_template
    assert "proxy_pass http://frontend:80;" in edge_template
    assert "return 308 https://${HTTPS_REDIRECT_HOST}${DOLLAR}request_uri;" in edge_template

    for variable in [
        "HTTP_PUBLISH_PORT",
        "HTTPS_PUBLISH_PORT",
        "HTTPS_SERVER_NAME",
        "HTTPS_REDIRECT_HOST",
    ]:
        assert f"{variable}=" in env_template


def test_neo4j_runtime_services_do_not_export_invalid_password_setting() -> None:
    compose = _read(DEPLOY / "docker-compose.yml")

    for service_name in ["neo4j-literature", "neo4j-patent"]:
        block = _service_block(compose, service_name)
        assert "NEO4J_AUTH: neo4j/${INTERNAL_NEO4J_PASSWORD:-lifeo4agent_neo4j_internal_123456}" in block
        assert "\n      NEO4J_PASSWORD:" not in block
        assert "NEO4J_HEALTHCHECK_PASSWORD:" not in block
        assert "HEALTHCHECK_NEO4J_PASSWORD: ${INTERNAL_NEO4J_PASSWORD:-lifeo4agent_neo4j_internal_123456}" in block
        assert "cypher-shell -u neo4j" in block
        assert "$${HEALTHCHECK_NEO4J_PASSWORD}" in block


def test_offline_export_includes_business_and_infrastructure_images() -> None:
    content = _read(DEPLOY / "scripts" / "export_images.sh")

    for variable in [
        "GATEWAY_IMAGE",
        "PUBLIC_SERVICE_IMAGE",
        "FASTQA_IMAGE",
        "HIGHTHINKINGQA_IMAGE",
        "PATENT_IMAGE",
        "FRONTEND_IMAGE",
    ]:
        assert variable in content

    for image_ref in [
        'MYSQL_IMAGE_TAG="${MYSQL_IMAGE_TAG:-8.0}"',
        'REDIS_IMAGE_TAG="${REDIS_IMAGE_TAG:-7}"',
        'MINIO_IMAGE_TAG="${MINIO_IMAGE_TAG:-latest}"',
        'MINIO_MC_IMAGE_TAG="${MINIO_MC_IMAGE_TAG:-latest}"',
        'NEO4J_IMAGE_TAG="${NEO4J_IMAGE_TAG:-5.26.12}"',
        'NGINX_IMAGE_TAG="${NGINX_IMAGE_TAG:-1.27-alpine}"',
        'SEED_TOOLS_IMAGE="${SEED_TOOLS_IMAGE:-lifeo4agent/seed-tools:latest}"',
    ]:
        assert image_ref in content


def test_python_base_image_includes_neo4j_driver() -> None:
    content = _read(DEPLOY / "docker" / "base.Dockerfile")

    assert "\n      neo4j\n" in content


def test_preflight_requires_mysql_admin_seed() -> None:
    content = _read(DEPLOY / "scripts" / "preflight_check.sh")

    assert "$DEPLOY_DIR/mysql-init/003_seed_admin.sql" in content


def test_dev_tls_generator_preserves_existing_pair_when_ca_pair_is_invalid(tmp_path: Path) -> None:
    _require_openssl()
    cert_dir = tmp_path / "certs"
    cert_dir.mkdir()
    shutil.copy2(DEPLOY / "certs" / "fullchain.pem", cert_dir / "fullchain.pem")
    shutil.copy2(DEPLOY / "certs" / "privkey.pem", cert_dir / "privkey.pem")
    old_fullchain = (cert_dir / "fullchain.pem").read_bytes()
    old_privkey = (cert_dir / "privkey.pem").read_bytes()

    subprocess.run(
        ["openssl", "genrsa", "-out", str(cert_dir / "rootCA.key"), "2048"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(cert_dir / "other-root.key"),
            "-out",
            str(cert_dir / "rootCA.pem"),
            "-days",
            "1",
            "-subj",
            "/C=CN/O=LiFeO4Agent/CN=Different Test Root CA",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    env = dict(os.environ)
    env["CERT_DIR"] = str(cert_dir)
    result = subprocess.run(
        ["bash", str(DEPLOY / "scripts" / "generate_dev_tls_cert.sh"), "broken-ca.test", "127.0.0.1"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    assert result.returncode != 0
    assert (cert_dir / "fullchain.pem").read_bytes() == old_fullchain
    assert (cert_dir / "privkey.pem").read_bytes() == old_privkey


def test_dev_tls_generator_uses_legacy_compatible_san_extfile() -> None:
    content = _read(DEPLOY / "scripts" / "generate_dev_tls_cert.sh")

    assert "-copy_extensions" not in content
    assert "-addext" not in content
    assert "subjectAltName" in content
    assert '-extfile "$TMP_SERVER_EXT"' in content
    assert "-extensions v3_req" in content


def test_preflight_validates_tls_certificate_matches_private_key() -> None:
    content = _read(DEPLOY / "scripts" / "preflight_check.sh")

    assert "TLS certificate and private key do not match" in content
    assert 'openssl x509 -in "$1" -pubkey -noout' in content
    assert 'openssl pkey -in "$1" -pubout -outform DER' in content
    assert 'tls_cert_pubkey_hash "$DEPLOY_DIR/certs/fullchain.pem"' in content
    assert 'tls_key_pubkey_hash "$DEPLOY_DIR/certs/privkey.pem"' in content


def test_offline_dockerfiles_exist_for_patent_and_frontend() -> None:
    assert (DEPLOY / "docker" / "Dockerfile.patent").exists()
    assert (DEPLOY / "docker" / "Dockerfile.frontend-nginx").exists()


def test_service_dockerfiles_run_gunicorn_from_service_roots() -> None:
    hq_dockerfile = _read(DEPLOY / "docker" / "Dockerfile.highthinkingqa")
    patent_dockerfile = _read(DEPLOY / "docker" / "Dockerfile.patent")

    assert "WORKDIR /app/highThinkingQA" in hq_dockerfile
    assert '"-c", "server_fastapi/gunicorn.conf.py"' in hq_dockerfile
    assert "--chdir" not in hq_dockerfile

    assert "WORKDIR /app/patent" in patent_dockerfile
    assert '"-c", "server_fastapi/gunicorn.conf.py"' in patent_dockerfile
    assert "--chdir" not in patent_dockerfile


def test_patent_corpus_is_seed_data_not_docker_build_context() -> None:
    dockerignore = _read(ROOT / ".dockerignore")
    collect_seed = _read(DEPLOY / "scripts" / "collect_seed_data.sh")

    assert "resource/patentQA/" in dockerignore
    assert "PATENTQA_SRC" in collect_seed
    assert "vector_db_patent_abstracts" in collect_seed
    assert "vector_db_patent_chunks" in collect_seed


def test_minio_seed_collection_includes_patent_originals() -> None:
    collect_minio_seed = _read(DEPLOY / "scripts" / "collect_minio_seed.sh")
    readme = _read(DEPLOY / "minio-seed" / "README.md")

    assert "PATENT_ORIGINALS_SRC" in collect_minio_seed
    assert "--patent-only" in collect_minio_seed
    assert "build_patent_original_backfill_plan" in collect_minio_seed
    assert "discover_patent_source_dirs" in collect_minio_seed
    assert "patent/originals" in readme


def test_deploy_env_uses_simplified_model_connection_variables() -> None:
    compose = _read(DEPLOY / "docker-compose.yml")
    env_template = _read(DEPLOY / ".env.production.example")

    for variable in [
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "QA_EMBEDDING_BASE_URL",
        "QA_EMBEDDING_MODEL",
        "HIGHTHINKINGQA_EMBEDDING_API_KEY",
        "HIGHTHINKINGQA_EMBEDDING_BASE_URL",
        "HIGHTHINKINGQA_EMBEDDING_MODEL",
        "RERANK_BASE_URL",
        "RERANK_MODEL",
        "RERANK_API_KEY",
    ]:
        assert f"{variable}=" in env_template

    for removed_variable in [
        "FASTQA_OPENAI_API_KEY=",
        "FASTQA_OPENAI_BASE_URL=",
        "HIGHTHINKINGQA_LLM_API_KEY=",
        "HIGHTHINKINGQA_LLM_BASE_URL=",
        "HIGHTHINKINGQA_LLM_MODEL=",
        "PATENT_OPENAI_API_KEY=",
        "PATENT_OPENAI_BASE_URL=",
        "PATENT_OPENAI_MODEL=",
        "HIGHTHINKINGQA_OCR_API_KEY=",
        "HIGHTHINKINGQA_OCR_BASE_URL=",
        "HIGHTHINKINGQA_OCR_MODEL=",
    ]:
        assert removed_variable not in env_template

    for mapping in [
        "OPENAI_API_KEY: ${LLM_API_KEY}",
        "LLM_API_KEY: ${LLM_API_KEY}",
        "PATENT_OPENAI_API_KEY: ${LLM_API_KEY}",
        "HIGHTHINKINGQA_EMBEDDING_API_KEY: ${HIGHTHINKINGQA_EMBEDDING_API_KEY}",
        "HIGHTHINKINGQA_EMBEDDING_BASE_URL: ${HIGHTHINKINGQA_EMBEDDING_BASE_URL}",
        "HIGHTHINKINGQA_EMBEDDING_MODEL: ${HIGHTHINKINGQA_EMBEDDING_MODEL}",
        "EMBEDDING_MODEL_TYPE: remote",
        "EMBEDDING_API_URL: ${QA_EMBEDDING_BASE_URL}",
        "PATENT_EMBEDDING_API_URL: ${QA_EMBEDDING_BASE_URL}",
        "QA_RETRIEVAL_RERANK_BASE_URL: ${RERANK_BASE_URL:-}",
        "PATENT_STAGE2_RERANK_BASE_URL: ${RERANK_BASE_URL:-}",
    ]:
        assert mapping in compose

    assert "OCR_API_KEY:" not in compose
    assert "OCR_BASE_URL:" not in compose
    assert "OCR_MODEL:" not in compose


def test_fastqa_and_patent_receive_runtime_rerank_variables() -> None:
    compose = _read(DEPLOY / "docker-compose.yml")

    fastqa = _service_block(compose, "fastqa")
    patent = _service_block(compose, "patent")

    for block in [fastqa, patent]:
        assert _has_env_mapping(block, "RERANK_BASE_URL", "${RERANK_BASE_URL:-}")
        assert _has_env_mapping(block, "RERANK_MODEL", "${RERANK_MODEL:-}")
        assert _has_env_mapping(block, "RERANK_API_KEY", "${RERANK_API_KEY:-}")


def test_patent_receives_minio_originals_configuration() -> None:
    compose = _read(DEPLOY / "docker-compose.yml")
    patent = _service_block(compose, "patent")

    assert _has_env_mapping(patent, "MINIO_ENDPOINT", "minio:9000")
    assert _has_env_mapping(patent, "MINIO_ACCESS_KEY", "${MINIO_ROOT_USER}")
    assert _has_env_mapping(patent, "MINIO_SECRET_KEY", "${MINIO_ROOT_PASSWORD}")
    assert _has_env_mapping(patent, "MINIO_BUCKET", "${MINIO_BUCKET}")
    assert _has_env_mapping(patent, "MINIO_SECURE", '"0"')
    assert _has_env_mapping(patent, "MINIO_REGION", "${MINIO_REGION:-us-east-1}")


def test_deploy_env_exposes_only_customer_facing_configuration() -> None:
    compose = _read(DEPLOY / "docker-compose.yml")
    env_template = _read(DEPLOY / ".env.production.example")

    customer_keys = {
        line.split("=", 1)[0]
        for line in env_template.splitlines()
        if line and not line.startswith("#") and "=" in line
    }
    expected_customer_keys = {
        "HTTP_PUBLISH_PORT",
        "HTTPS_PUBLISH_PORT",
        "HTTPS_SERVER_NAME",
        "HTTPS_REDIRECT_HOST",
        "FRONTEND_PUBLISH_PORT",
        "MYSQL_PUBLISH_PORT",
        "REDIS_PUBLISH_PORT",
        "MINIO_API_PUBLISH_PORT",
        "MINIO_CONSOLE_PUBLISH_PORT",
        "MYSQL_ROOT_PASSWORD",
        "MYSQL_APP_USER",
        "MYSQL_APP_PASSWORD",
        "REDIS_PASSWORD",
        "MINIO_ROOT_USER",
        "MINIO_ROOT_PASSWORD",
        "MINIO_BUCKET",
        "MINIO_REGION",
        "DATA_PACKAGE_VERSION",
        "DATA_SEED_FORCE",
        "JWT_SECRET",
        "PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN",
        "LLM_API_KEY",
        "LLM_AUTH_MODE",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "LLM_IS_THINKING_MODEL",
        "LLM_THINKING_ENABLED",
        "INTENT_MODEL_ENABLED",
        "INTENT_MODEL_API_KEY",
        "INTENT_MODEL_AUTH_MODE",
        "INTENT_MODEL_BASE_URL",
        "INTENT_MODEL",
        "INTENT_MODEL_TIMEOUT_SECONDS",
        "QA_STAGE1_LOG_RESPONSE_MAX_CHARS",
        "QA_STAGE1_LOG_FULL_RESPONSE",
        "QA_STAGE2_DIAGNOSTIC_LOG",
        "QA_STAGE2_LOG_QUERY_DETAILS",
        "QA_STAGE2_LOG_HIT_DETAILS",
        "QA_STAGE2_LOG_HIT_MAX",
        "QA_STAGE2_LOG_QUERY_MAX_CHARS",
        "QA_STAGE3_DIAGNOSTIC_LOG",
        "QA_STAGE3_LOG_SOURCE_DETAILS",
        "QA_STAGE3_LOG_CHUNK_DETAILS",
        "QA_STAGE3_LOG_CHUNK_MAX",
        "QA_STAGE3_LOG_TEXT_MAX_CHARS",
        "FASTQA_STAGE2_CHAT_WARMUP_ENABLED",
        "FASTQA_STAGE2_RERANK_WARMUP_ENABLED",
        "PDF_QA_WARMUP_ENABLED",
        "PATENT_PLANNING_HOT_POOL_WARMUP_ENABLED",
        "QA_EMBEDDING_API_KEY",
        "QA_EMBEDDING_AUTH_MODE",
        "QA_EMBEDDING_BASE_URL",
        "QA_EMBEDDING_MODEL",
        "HIGHTHINKINGQA_EMBEDDING_API_KEY",
        "HIGHTHINKINGQA_EMBEDDING_AUTH_MODE",
        "HIGHTHINKINGQA_EMBEDDING_BASE_URL",
        "HIGHTHINKINGQA_EMBEDDING_MODEL",
        "RERANK_BASE_URL",
        "RERANK_MODEL",
        "RERANK_API_KEY",
        "RERANK_AUTH_MODE",
    }

    assert customer_keys == expected_customer_keys

    hidden_internal_keys = {
        "QA_EMBEDDING_MODEL_TYPE",
        "MYSQL_DATABASE",
        "PUBLIC_SERVICE_REDIS_ENABLED",
        "PUBLIC_SERVICE_REDIS_DB",
        "PUBLIC_SERVICE_REDIS_KEY_PREFIX",
        "FASTQA_REDIS_ENABLED",
        "FASTQA_REDIS_DB",
        "FASTQA_REDIS_KEY_PREFIX",
        "HIGHTHINKINGQA_REDIS_ENABLED",
        "HIGHTHINKINGQA_REDIS_DB",
        "HIGHTHINKINGQA_REDIS_KEY_PREFIX",
        "PATENT_REDIS_ENABLED",
        "PATENT_REDIS_DB",
        "PATENT_REDIS_KEY_PREFIX",
        "PATENT_GUNICORN_WORKERS",
        "PATENT_GUNICORN_THREADS",
        "PATENT_GUNICORN_TIMEOUT",
        "PATENT_GRAPH_KB_ENABLED",
        "PATENT_GRAPH_KB_V2_ENABLED",
        "PATENT_GRAPH_KB_RAG_INJECTION_ENABLED",
        "GATEWAY_CONVERSATION_FILE_PROVIDER",
        "GATEWAY_REQUEST_TIMEOUT_SECONDS",
        "GATEWAY_SSE_TIMEOUT_SECONDS",
        "PUBLIC_SERVICE_CORS_ORIGINS",
        "PUBLIC_SERVICE_MINIO_USE_PROXY",
        "PUBLIC_SERVICE_MINIO_DOWNLOAD_EXPIRES",
        "PATENT_NEO4J_URL",
        "PATENT_NEO4J_USERNAME",
        "PATENT_NEO4J_PASSWORD",
        "PATENT_NEO4J_DATABASE",
        "GATEWAY_PUBLISH_PORT",
        "PUBLIC_SERVICE_PUBLISH_PORT",
        "FASTQA_PUBLISH_PORT",
        "HIGHTHINKINGQA_PUBLISH_PORT",
        "PATENT_PUBLISH_PORT",
    }
    for key in hidden_internal_keys:
        assert f"{key}=" not in env_template

    for fixed_mapping in [
        "MYSQL_DATABASE: agentcode",
        "REDIS_DB: 0",
        "REDIS_KEY_PREFIX: fastqa",
        "REDIS_KEY_PREFIX: highthinkingqa",
        "PATENT_REDIS_URL: redis://:${REDIS_PASSWORD}@redis:6379/0",
        "PATENT_GUNICORN_WORKERS: 8",
        "GATEWAY_CONVERSATION_FILE_PROVIDER: public_http",
    ]:
        assert fixed_mapping in compose
