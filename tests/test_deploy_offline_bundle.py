from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_offline_compose_includes_patent_and_frontend_services() -> None:
    content = _read(DEPLOY / "docker-compose.yml")

    assert "  patent:" in content
    assert "image: ${PATENT_IMAGE}" in content
    assert "PATENT_PORT: 8010" in content
    assert "PATENT_AUTHORITY_BASE_URL: http://public-service:8102" in content
    assert "PATENT_NEO4J_URL: ${PATENT_NEO4J_URL}" in content
    assert "- patentqa_data:/app/resource/patentQA" in content

    assert "  frontend:" in content
    assert "image: ${FRONTEND_IMAGE}" in content
    assert '"${FRONTEND_PUBLISH_PORT}:80"' in content
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
        "mysql:${MYSQL_IMAGE_TAG}",
        "redis:${REDIS_IMAGE_TAG}",
        "minio/minio:${MINIO_IMAGE_TAG}",
        "minio/mc:${MINIO_MC_IMAGE_TAG}",
        "alpine:${ALPINE_IMAGE_TAG}",
        "nginx:${NGINX_IMAGE_TAG}",
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
        "QA_EMBEDDING_MODEL_TYPE",
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
        "EMBEDDING_API_URL: ${QA_EMBEDDING_BASE_URL}",
        "PATENT_EMBEDDING_API_URL: ${QA_EMBEDDING_BASE_URL}",
        "QA_RETRIEVAL_RERANK_PROVIDER: ${RERANK_PROVIDER}",
        "PATENT_STAGE2_RERANK_PROVIDER: ${RERANK_PROVIDER}",
    ]:
        assert mapping in compose

    assert "OCR_API_KEY:" not in compose
    assert "OCR_BASE_URL:" not in compose
    assert "OCR_MODEL:" not in compose
