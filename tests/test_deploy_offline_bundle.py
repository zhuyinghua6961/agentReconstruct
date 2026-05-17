from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_offline_compose_includes_patent_and_frontend_services() -> None:
    content = _read(DEPLOY / "docker-compose.yml")

    assert "  patent:" in content
    assert "image: ${PATENT_IMAGE:-highthinking/patent:latest}" in content
    assert "PATENT_PORT: 8010" in content
    assert "PATENT_AUTHORITY_BASE_URL: http://public-service:8102" in content
    assert "PATENT_NEO4J_URL: ${PATENT_NEO4J_URL}" in content
    assert "- patentqa_data:/app/resource/patentQA" in content

    assert "  frontend:" in content
    assert "image: ${FRONTEND_IMAGE:-highthinking/frontend:latest}" in content
    assert '"${FRONTEND_PUBLISH_PORT:-8080}:80"' in content
    assert "gateway:" in content


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
        'ALPINE_IMAGE_TAG="${ALPINE_IMAGE_TAG:-3.20}"',
        'NGINX_IMAGE_TAG="${NGINX_IMAGE_TAG:-1.27-alpine}"',
    ]:
        assert image_ref in content


def test_offline_dockerfiles_exist_for_patent_and_frontend() -> None:
    assert (DEPLOY / "docker" / "Dockerfile.patent").exists()
    assert (DEPLOY / "docker" / "Dockerfile.frontend-nginx").exists()


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
        "RERANK_PROVIDER",
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
        "EMBEDDING_MODEL_TYPE: remote",
        "EMBEDDING_API_URL: ${QA_EMBEDDING_BASE_URL}",
        "PATENT_EMBEDDING_API_URL: ${QA_EMBEDDING_BASE_URL}",
        "QA_RETRIEVAL_RERANK_PROVIDER: ${RERANK_PROVIDER}",
        "PATENT_STAGE2_RERANK_PROVIDER: ${RERANK_PROVIDER}",
    ]:
        assert mapping in compose

    assert "OCR_API_KEY:" not in compose
    assert "OCR_BASE_URL:" not in compose
    assert "OCR_MODEL:" not in compose


def test_deploy_env_exposes_only_customer_facing_configuration() -> None:
    compose = _read(DEPLOY / "docker-compose.yml")
    env_template = _read(DEPLOY / ".env.production.example")

    customer_keys = {
        line.split("=", 1)[0]
        for line in env_template.splitlines()
        if line and not line.startswith("#") and "=" in line
    }
    expected_customer_keys = {
        "FRONTEND_PUBLISH_PORT",
        "GATEWAY_PUBLISH_PORT",
        "PUBLIC_SERVICE_PUBLISH_PORT",
        "FASTQA_PUBLISH_PORT",
        "HIGHTHINKINGQA_PUBLISH_PORT",
        "PATENT_PUBLISH_PORT",
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
        "JWT_SECRET",
        "PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN",
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "QA_EMBEDDING_API_KEY",
        "QA_EMBEDDING_BASE_URL",
        "QA_EMBEDDING_MODEL",
        "HIGHTHINKINGQA_EMBEDDING_API_KEY",
        "HIGHTHINKINGQA_EMBEDDING_BASE_URL",
        "HIGHTHINKINGQA_EMBEDDING_MODEL",
        "RERANK_PROVIDER",
        "RERANK_BASE_URL",
        "RERANK_MODEL",
        "RERANK_API_KEY",
        "PATENT_NEO4J_URL",
        "PATENT_NEO4J_USERNAME",
        "PATENT_NEO4J_PASSWORD",
        "PATENT_NEO4J_DATABASE",
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
