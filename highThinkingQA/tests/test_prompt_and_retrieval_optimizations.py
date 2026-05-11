import builtins
import asyncio

from retriever.vector_retriever import batch_retrieve
from agent_core.llm_client import load_prompt_template
from agent_core.sub_answerer import iter_pre_answers_async
from ingest.vector_store import get_chroma_client, get_or_create_collection


def test_load_prompt_template_is_cached(monkeypatch):
    call_count = {"n": 0}

    def fake_open(path, mode="r", encoding=None):
        class _Reader:
            def __enter__(self):
                call_count["n"] += 1
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return "template-content"

        return _Reader()

    monkeypatch.setattr(builtins, "open", fake_open)
    monkeypatch.setattr("agent_core.llm_client.config.PROMPTS_DIR", "/tmp/prompts")
    load_prompt_template.__globals__["_load_prompt_template_cached"].cache_clear()

    assert load_prompt_template("demo.txt") == "template-content"
    assert load_prompt_template("demo.txt") == "template-content"
    assert call_count["n"] == 1


def test_batch_retrieve_batches_embedding_call(monkeypatch):
    queries = ["q1", "q2"]
    embedding_calls = {"n": 0}

    monkeypatch.setattr(
        "retriever.vector_retriever.embed_texts",
        lambda texts, client=None: embedding_calls.__setitem__("n", embedding_calls["n"] + 1) or [[0.1], [0.2]],
    )
    monkeypatch.setattr(
        "retriever.vector_retriever.batch_query_collection",
        lambda query_embeddings, top_k=None, collection=None: {
            "ids": [["a"], ["b"]],
            "documents": [["doc-a"], ["doc-b"]],
            "metadatas": [[{"doi": "10.1/a", "title": "A", "section_name": "S1", "chunk_index": 1}],
                          [{"doi": "10.1/b", "title": "B", "section_name": "S2", "chunk_index": 2}]],
            "distances": [[0.01], [0.02]],
        },
    )

    results = batch_retrieve(queries, top_k=3, collection=object(), embedding_client=object())

    assert embedding_calls["n"] == 1
    assert len(results) == 2
    assert results[0][0].doi == "10.1/a"
    assert results[1][0].doi == "10.1/b"


def test_iter_pre_answers_async_yields_completion_order(monkeypatch):
    async def fake_async_pre_answer(sub_question, async_client, original_question=None):
        delays = {"q1": 0.03, "q2": 0.0, "q3": 0.01}
        await asyncio.sleep(delays[sub_question])
        return f"answer:{sub_question}"

    monkeypatch.setattr("agent_core.sub_answerer._async_pre_answer", fake_async_pre_answer)

    async def _collect():
        items = []
        async for item in iter_pre_answers_async(["q1", "q2", "q3"], async_client=object()):
            items.append(item)
        return items

    results = asyncio.run(_collect())

    assert results == [
        (1, "answer:q2"),
        (2, "answer:q3"),
        (0, "answer:q1"),
    ]


def test_vector_store_default_client_and_collection_are_cached(monkeypatch):
    created_clients = []

    class DummyClient:
        def __init__(self, path):
            self.path = path

        def get_or_create_collection(self, name, metadata):
            return {"path": self.path, "name": name, "metadata": metadata}

    def fake_client(path):
        created_clients.append(path)
        return DummyClient(path)

    monkeypatch.setattr("ingest.vector_store.chromadb.PersistentClient", fake_client)
    monkeypatch.setattr("ingest.vector_store.config.CHROMA_PERSIST_DIR", "/tmp/chroma-a")
    monkeypatch.setattr("ingest.vector_store.config.CHROMA_COLLECTION_NAME", "demo")
    get_chroma_client.__globals__["_get_chroma_client_cached"].cache_clear()
    get_or_create_collection.__globals__["_get_default_collection_cached"].cache_clear()

    client_a = get_chroma_client()
    client_b = get_chroma_client()
    collection_a = get_or_create_collection()
    collection_b = get_or_create_collection()

    assert client_a is client_b
    assert collection_a is collection_b
    assert created_clients == ["/tmp/chroma-a"]


def test_get_or_create_collection_logs_runtime_resource_status(monkeypatch, caplog, tmp_path):
    class DummyClient:
        def get_or_create_collection(self, name, metadata):
            return {"name": name, "metadata": metadata}

    monkeypatch.setattr("ingest.vector_store._get_chroma_client_cached", lambda path: DummyClient())
    monkeypatch.setattr("ingest.vector_store.config.CHROMA_PERSIST_DIR", str(tmp_path / "vectordb"))
    monkeypatch.setattr("ingest.vector_store.config.CHROMA_COLLECTION_NAME", "demo")
    get_or_create_collection.__globals__["_get_default_collection_cached"].cache_clear()

    caplog.set_level("INFO")
    collection = get_or_create_collection()

    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert "vector_store collection ready" in joined
    assert "demo" in joined
    assert str(tmp_path / "vectordb") in joined
    assert collection["name"] == "demo"
