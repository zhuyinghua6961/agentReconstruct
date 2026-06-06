from __future__ import annotations

from fastapi.testclient import TestClient


def test_local_embedding_server_exposes_openai_compatible_embeddings():
    from local_embedding_server import EmbeddingServerSettings, create_app

    calls = []

    def fake_embed(texts, *, settings, dimensions):
        calls.append((list(texts), settings.model_path, dimensions))
        return [[0.1, 0.2], [0.3, 0.4]]

    app = create_app(
        settings=EmbeddingServerSettings(
            model_name="qwen3-embedding-8b",
            model_path="/models/qwen3",
            dimensions=2,
            allow_dimensions_parameter=True,
            batch_size=4,
            max_input_tokens=128,
            device="cpu",
            api_key="",
        ),
        embed_func=fake_embed,
    )
    client = TestClient(app)

    response = client.post(
        "/v1/embeddings",
        json={
            "model": "qwen3-embedding-8b",
            "input": ["alpha", "beta"],
            "dimensions": 2,
            "encoding_format": "float",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["object"] == "list"
    assert payload["model"] == "qwen3-embedding-8b"
    assert payload["data"] == [
        {"object": "embedding", "embedding": [0.1, 0.2], "index": 0},
        {"object": "embedding", "embedding": [0.3, 0.4], "index": 1},
    ]
    assert payload["usage"]["prompt_tokens"] >= 2
    assert calls == [(["alpha", "beta"], "/models/qwen3", 2)]


def test_local_embedding_server_accepts_single_string_input():
    from local_embedding_server import EmbeddingServerSettings, create_app

    def fake_embed(texts, *, settings, dimensions):
        return [[1.0] * dimensions for _ in texts]

    app = create_app(
        settings=EmbeddingServerSettings(
            model_name="demo",
            model_path="/models/qwen3",
            dimensions=3,
            batch_size=1,
            max_input_tokens=128,
            device="",
            api_key="",
        ),
        embed_func=fake_embed,
    )
    client = TestClient(app)

    response = client.post("/v1/embeddings", json={"model": "demo", "input": "hello"})

    assert response.status_code == 200
    assert response.json()["data"][0]["embedding"] == [1.0, 1.0, 1.0]


def test_local_embedding_server_rejects_unsupported_dimensions():
    from local_embedding_server import EmbeddingServerSettings, create_app

    app = create_app(
        settings=EmbeddingServerSettings(
            model_name="demo",
            model_path="/models/qwen3",
            dimensions=4096,
            allow_dimensions_parameter=True,
            batch_size=1,
            max_input_tokens=128,
            device="",
            api_key="",
        ),
        embed_func=lambda texts, *, settings, dimensions: [],
    )
    client = TestClient(app)

    response = client.post("/v1/embeddings", json={"model": "demo", "input": "hello", "dimensions": 4097})

    assert response.status_code == 400
    assert "dimensions" in response.json()["detail"]


def test_local_embedding_server_rejects_dimensions_parameter_by_default():
    from fastapi import HTTPException
    from local_embedding_server import EmbeddingServerSettings, _resolve_dimensions

    settings = EmbeddingServerSettings(
        model_name="demo",
        model_path="/models/qwen3",
        dimensions=4096,
        batch_size=1,
        max_input_tokens=128,
        device="",
        api_key="",
    )

    try:
        _resolve_dimensions(4096, settings)
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "dimensions" in str(exc.detail)
    else:
        raise AssertionError("dimensions parameter should be rejected by default")


def test_local_embedding_server_allows_dimensions_parameter_when_enabled():
    from local_embedding_server import EmbeddingServerSettings, _resolve_dimensions

    settings = EmbeddingServerSettings(
        model_name="demo",
        model_path="/models/qwen3",
        dimensions=4096,
        allow_dimensions_parameter=True,
        batch_size=1,
        max_input_tokens=128,
        device="",
        api_key="",
    )

    assert _resolve_dimensions(2048, settings) == 2048


def test_local_embedding_server_optional_bearer_auth():
    from local_embedding_server import EmbeddingServerSettings, create_app

    app = create_app(
        settings=EmbeddingServerSettings(
            model_name="demo",
            model_path="/models/qwen3",
            dimensions=2,
            batch_size=1,
            max_input_tokens=128,
            device="",
            api_key="secret",
        ),
        embed_func=lambda texts, *, settings, dimensions: [[0.0] * dimensions for _ in texts],
    )
    client = TestClient(app)

    assert client.post("/v1/embeddings", json={"model": "demo", "input": "hello"}).status_code == 401
    assert client.post(
        "/v1/embeddings",
        headers={"Authorization": "Bearer secret"},
        json={"model": "demo", "input": "hello"},
    ).status_code == 200
