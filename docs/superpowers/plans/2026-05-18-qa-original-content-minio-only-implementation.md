# QA Original Content MinIO-only Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move runtime loading of QA originals, uploaded PDFs/tables, patent structured originals, and patent table supplements to MinIO-only reads, with no local file fallback in the target path.

**Architecture:** Each service gets a small local object reader abstraction that reads MinIO bytes, JSON, object stat, and scratch files from `storage_ref` or manifest object names. Gateway/public-service preserve `storage_ref` as the execution contract, while `fastQA`, `patent`, and legacy `highThinkingQA` document paths stop treating `local_path` as executable input. Patent KB table loading moves from archive `*_tables.json` to `patent/originals/{id}/manifest.json -> structured/tables.json`.

**Tech Stack:** Python, FastAPI service modules, MinIO Python SDK, pandas/openpyxl/PyMuPDF path-compatible scratch files, pytest, Node test runner for existing frontend regression only if route output changes.

**Source Spec:** `docs/2026-05-18-qa-original-content-minio-only-spec.md`

**Commit Policy:** This plan is documentation-only and should not be committed unless the user explicitly asks. During implementation, use `git status` checkpoints after each task; create commits only after user approval.

---

## File Structure

### New Shared-by-Pattern Readers

Create service-local readers instead of a cross-package dependency:

- `fastQA/app/modules/storage/object_reader.py`
  - Parse `minio://bucket/object` refs.
  - Read bytes/json/stat from MinIO.
  - Materialize scratch files under `FASTQA_SERVICE_RUNTIME_ROOT/object-cache` or `FASTQA_UPLOAD_CACHE_DIR`.
  - Reject `local://` and bare local paths in strict mode.

- `patent/server/patent/object_reader.py`
  - Same object reader capabilities for patent service.
  - Exposes object-name reads for `patent/originals/...` manifest paths.
  - Uses fake backend/client in unit tests.

- `highThinkingQA/server/storage/object_reader.py`
  - Same object reader capabilities for legacy highThinkingQA document routes.
  - Uses existing MinIO env conventions from `server/storage/minio_backend.py`.

### Gateway/Public-Service Contract Files

- Modify `gateway/app/models/files.py`
  - Add `has_minio_storage_ref` helper.

- Modify `gateway/app/services/file_context_resolver.py`
  - Count and surface local-only selected files.
  - Reject local-only execution files when strict mode is enabled.

- Modify `public-service/backend/app/modules/documents/schemas.py`
  - Add `tables` to patent manifest schema if missing.

- Modify `public-service/backend/app/modules/documents/patent_original_store.py`
  - Treat table availability according to spec defaults.

- Modify `public-service/backend/tests/test_patent_original_view_module.py`
  - Add manifest/table schema tests.

### fastQA Files

- Modify `fastQA/app/modules/storage/upload_materializer.py`
  - Replace local-first behavior with MinIO-only strict behavior.
  - Keep legacy local fallback only behind explicit rollback flag.

- Modify `fastQA/app/modules/storage/uploaded_file_storage.py`
  - Re-export new MinIO-only reader helpers.

- Modify `fastQA/app/modules/qa_tabular/workbook_loader.py`
  - Build workbook signatures from storage object stat, not `local_path` stat.
  - Load CSV/XLS/XLSX via MinIO bytes or scratch file.

- Modify `fastQA/app/services/file_routes.py`
  - Require MinIO-backed execution files for PDF/table/hybrid file routes.

- Modify `fastQA/app/modules/storage/paper_storage.py`
  - Add MinIO-only paper lookup/materialization path.
  - Stop checking local paper files in strict mode.

- Update tests:
  - `fastQA/tests/test_upload_materializer.py`
  - `fastQA/tests/test_workbook_loader_storage_ref.py`
  - `fastQA/tests/test_file_routes_materialization.py`
  - `fastQA/tests/test_documents_storage.py`

### patent Files

- Create `patent/server/patent/original_minio_loader.py`
  - Load `manifest.json`.
  - Convert `structured/tables.json` rows into `PatentTableSupplement`.
  - Return empty table list with diagnostics for `original_manifest_unavailable`, `tables_unavailable`, and `tables_object_unavailable`.

- Modify `patent/server/patent/runtime.py`
  - Wire `PatentOriginalMinioLoader.load_tables` into retrieval/evidence loading when `PATENT_ORIGINAL_MINIO_ONLY=true`.

- Modify `patent/server/patent/archive_loader.py`
  - Keep local archive loading for catalog bootstrap only.
  - Do not use local `load_tables()` in strict MinIO-only table supplement path.

- Modify `patent/server/patent/file_contract.py`
  - Require selected execution files to have `minio://` `storage_ref`.
  - Stop deriving spreadsheet suffix from `local_path`.

- Modify `patent/server/patent/tabular/workbook_loader.py`
  - Add load-from-storage-ref/scratch wrapper.

- Modify `patent/server/patent/tabular_service.py`
  - Replace direct `local_path` reads in `_load_table_descriptors()` and `_load_table_text()`.

- Update tests:
  - `patent/tests/test_patent_retrieval_service.py`
  - `patent/tests/test_patent_stage3_evidence_loading.py`
  - `patent/tests/test_patent_file_contract.py` if present, otherwise create it.
  - `patent/tests/test_patent_tabular_service.py`

### highThinkingQA Files

- Modify `highThinkingQA/server/storage/paper_storage.py`
  - Add MinIO-only `ensure_scratch_paper_pdf()` behavior.
  - Stop returning existing local paper files in strict mode.

- Modify `highThinkingQA/server/services/documents_service.py`
  - Use MinIO scratch materialization for document view/download/translation.

- Update tests:
  - `highThinkingQA/tests/test_documents_service.py`

---

## Task 1: fastQA Object Reader Foundation

**Files:**
- Create: `fastQA/app/modules/storage/object_reader.py`
- Modify: `fastQA/app/modules/storage/uploaded_file_storage.py`
- Test: `fastQA/tests/test_upload_materializer.py`
- Test: `fastQA/tests/test_workbook_loader_storage_ref.py`

- [ ] **Step 1: Write failing tests for MinIO-only object reader**

Add tests covering:

```python
def test_object_reader_reads_minio_bytes_without_local_path(fake_minio):
    fake_minio.put("agentcode", "uploads/a.csv", b"a,b\n1,2\n", etag="e1")
    reader = ObjectReader(client=fake_minio, runtime_root=tmp_path)

    assert reader.read_bytes("minio://agentcode/uploads/a.csv") == b"a,b\n1,2\n"


def test_object_reader_rejects_local_storage_ref(tmp_path):
    local = tmp_path / "a.csv"
    local.write_text("a,b\n1,2\n", encoding="utf-8")
    reader = ObjectReader(client=FakeMinio(), runtime_root=tmp_path)

    with pytest.raises(ObjectReaderProtocolError):
        reader.read_bytes(f"local://{local}")
```

Add scratch key edge cases:

```python
def test_object_reader_scratch_key_includes_sha256_metadata(fake_minio, tmp_path):
    fake_minio.put("agentcode", "uploads/a.csv", b"a,b\n1,2\n", etag="same", metadata={"sha256": "sha-a"})
    fake_minio.put("agentcode", "uploads/b.csv", b"a,b\n1,2\n", etag="same", metadata={"sha256": "sha-b"})
    reader = ObjectReader(client=fake_minio, runtime_root=tmp_path)

    path_a = reader.materialize_temp("minio://agentcode/uploads/a.csv", suffix=".csv")
    path_b = reader.materialize_temp("minio://agentcode/uploads/b.csv", suffix=".csv")

    assert path_a != path_b
    assert path_a.read_bytes() == b"a,b\n1,2\n"


def test_object_reader_scratch_key_computes_sha256_when_etag_missing(fake_minio, tmp_path):
    fake_minio.put("agentcode", "uploads/a.csv", b"a,b\n1,2\n", etag="")
    reader = ObjectReader(client=fake_minio, runtime_root=tmp_path)

    path = reader.materialize_temp("minio://agentcode/uploads/a.csv", suffix=".csv")

    assert path.read_bytes() == b"a,b\n1,2\n"
    assert path.name.endswith(".csv")
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
conda run -n agent python -m pytest fastQA/tests/test_upload_materializer.py fastQA/tests/test_workbook_loader_storage_ref.py -q
```

Expected: fails because `ObjectReader` and strict MinIO-only behavior do not exist yet.

- [ ] **Step 3: Implement `fastQA/app/modules/storage/object_reader.py`**

Required behavior:

```python
@dataclass(frozen=True)
class ObjectStat:
    bucket: str
    object_name: str
    etag: str
    size: int


class ObjectReaderProtocolError(RuntimeError): ...
class ObjectReaderUnavailableError(RuntimeError): ...


class ObjectReader:
    def read_bytes(self, storage_ref: str) -> bytes: ...
    def read_json(self, storage_ref: str) -> Any: ...
    def stat(self, storage_ref: str) -> ObjectStat: ...
    def materialize_temp(self, storage_ref: str, *, suffix: str) -> Path: ...
```

Implementation notes:

- Accept only `minio://bucket/object`.
- Build MinIO client from `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_SECURE`.
- Return `ObjectReaderProtocolError` for missing/invalid refs.
- Return `ObjectReaderUnavailableError` for missing object or storage failures.
- Scratch path key:
  - Use sha1 of `bucket|object_name|etag|size|sha256` when object/user metadata includes sha256.
  - Use sha1 of `bucket|object_name|etag|size` when etag is present and sha256 is absent.
  - If etag is unavailable, read bytes, compute sha256, and use sha1 of `bucket|object_name|size|computed_sha256`.
- Scratch file contents must come from `read_bytes()`.

- [ ] **Step 4: Re-export reader helpers**

Update `fastQA/app/modules/storage/uploaded_file_storage.py`:

```python
from app.modules.storage.object_reader import (
    ObjectReader,
    ObjectReaderProtocolError,
    ObjectReaderUnavailableError,
    ObjectStat,
)
```

- [ ] **Step 5: Run foundation tests**

Run:

```bash
conda run -n agent python -m pytest fastQA/tests/test_upload_materializer.py fastQA/tests/test_workbook_loader_storage_ref.py -q
```

Expected: object reader tests pass; older local-first tests may still fail and should be updated in Task 2.

- [ ] **Step 6: Checkpoint**

Run:

```bash
git status --short
```

Expected: only fastQA reader and tests changed for this task.

---

## Task 2: fastQA Uploaded PDF/Table Execution Uses MinIO

**Files:**
- Modify: `fastQA/app/modules/storage/upload_materializer.py`
- Modify: `fastQA/app/modules/qa_tabular/workbook_loader.py`
- Modify: `fastQA/app/services/file_routes.py`
- Test: `fastQA/tests/test_upload_materializer.py`
- Test: `fastQA/tests/test_workbook_loader_storage_ref.py`
- Test: `fastQA/tests/test_file_routes_materialization.py`
- Test: `fastQA/tests/test_file_routes_tabular_kb.py`

- [ ] **Step 1: Update tests to lock MinIO-only behavior**

Add or update tests:

```python
def test_materialize_uploaded_file_ignores_existing_local_path_when_storage_ref_missing(tmp_path):
    local = tmp_path / "old.pdf"
    local.write_bytes(b"%PDF-old")
    item = {"file_id": 1, "file_type": "pdf", "local_path": str(local), "storage_ref": ""}

    prepared = materialize_uploaded_file(item, strict_minio_only=True)

    assert prepared["local_path"] == ""
    assert prepared["storage_error"] == "storage_ref_missing"
```

```python
def test_workbook_loader_reads_xlsx_from_minio_when_local_missing(fake_minio, tmp_path):
    fake_minio.put("agentcode", "uploads/book.xlsx", build_xlsx_bytes(), etag="book-etag")
    workbook = load_workbook({
        "file_id": 7,
        "file_name": "book.xlsx",
        "file_type": "excel",
        "storage_ref": "minio://agentcode/uploads/book.xlsx",
        "local_path": str(tmp_path / "missing.xlsx"),
    })

    assert workbook["storage_ref"] == "minio://agentcode/uploads/book.xlsx"
    assert workbook["local_path"] == ""
    assert workbook["sheets"]
```

```python
def test_pdf_route_rejects_local_only_file_even_when_local_path_exists(tmp_path):
    local = tmp_path / "a.pdf"
    local.write_bytes(b"%PDF-local")
    # route should emit execution_file_unavailable/storage_ref_missing, not read local
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
conda run -n agent python -m pytest fastQA/tests/test_upload_materializer.py fastQA/tests/test_workbook_loader_storage_ref.py fastQA/tests/test_file_routes_materialization.py fastQA/tests/test_file_routes_tabular_kb.py -q
```

Expected: failures show local-first paths still active.

- [ ] **Step 3: Update upload materializer**

Change `materialize_uploaded_file()` so strict mode:

- Parses `storage_ref`.
- Rejects missing/non-MinIO refs.
- Downloads/materializes only from MinIO.
- Ignores `local_path` as input.
- Populates diagnostic fields:
  - `local_path=""` unless scratch materialization succeeds.
  - `storage_error="storage_ref_missing" | "storage_ref_not_minio" | "object_unavailable"`.
  - `storage_object_name`.
  - `storage_bucket`.

Keep rollback only behind an explicit flag:

```text
FASTQA_UPLOAD_MINIO_ONLY=false
```

The default for dev/staging code paths should be strict MinIO-only.

- [ ] **Step 4: Update workbook loader**

Change `build_file_signature()`:

- Use `storage_ref`, `file_id`, `file_name`, `status_updated_at`, bucket, object name, etag, size, and sha256 when present.
- If etag is unavailable, compute sha256 from bytes and use that computed digest in the signature.
- Do not stat `local_path`.

Change `load_workbook()`:

- For CSV: read bytes and decode using existing encoding loop, or materialize scratch with `.csv`.
- For XLS/XLSX/XLSM: materialize scratch with original suffix, then call pandas/openpyxl path loader.
- Return `"local_path": ""` in public result unless needed internally for debug.

- [ ] **Step 5: Update file route error handling**

In `fastQA/app/services/file_routes.py`:

- Treat missing `storage_ref` as a protocol error before selecting `pdf_path`.
- For PDF route, use MinIO materialized scratch path returned by object reader, not payload `local_path`.
- For table route, pass full file item to workbook loader and let it read MinIO.
- Error message should say the file must be re-uploaded or metadata refreshed.

- [ ] **Step 6: Run fastQA file tests**

Run:

```bash
conda run -n agent python -m pytest fastQA/tests/test_upload_materializer.py fastQA/tests/test_workbook_loader_storage_ref.py fastQA/tests/test_file_routes_materialization.py fastQA/tests/test_file_routes_tabular_kb.py fastQA/tests/test_qa_tabular_service.py -q
```

Expected: all selected fastQA tests pass.

- [ ] **Step 7: Checkpoint**

Run:

```bash
git status --short
```

Expected: fastQA implementation and tests only, plus existing uncommitted docs/deploy files.

---

## Task 3: fastQA Paper PDF MinIO-only Path

**Files:**
- Modify: `fastQA/app/modules/storage/paper_storage.py`
- Modify callers if needed:
  - `fastQA/app/modules/documents/service.py`
  - `fastQA/app/modules/generation_pipeline/context_loading.py`
  - `fastQA/app/modules/generation_pipeline/pdf_pipeline.py`
- Test: `fastQA/tests/test_documents_storage.py`
- Test: `fastQA/tests/test_documents.py`
- Test: `fastQA/tests/test_context_loading.py`

- [ ] **Step 1: Write failing tests**

Add tests:

```python
def test_ensure_local_paper_pdf_ignores_existing_local_when_minio_missing(monkeypatch, tmp_path):
    (tmp_path / "10.1000_demo.pdf").write_bytes(b"%PDF-local")
    monkeypatch.setenv("QA_ORIGINAL_MINIO_ONLY", "true")
    monkeypatch.setattr(paper_storage, "_build_minio_client_from_env", lambda: fake_missing_minio())

    assert ensure_local_paper_pdf(doi="10.1000/demo", papers_dir=tmp_path) is None
```

```python
def test_ensure_local_paper_pdf_materializes_from_minio_to_scratch(monkeypatch, tmp_path):
    monkeypatch.setenv("QA_ORIGINAL_MINIO_ONLY", "true")
    monkeypatch.setattr(paper_storage, "_build_minio_client_from_env", lambda: fake_minio_with_pdf())

    path = ensure_local_paper_pdf(doi="10.1000/demo", papers_dir=tmp_path)

    assert path is not None
    assert path.read_bytes() == b"%PDF-minio"
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
conda run -n agent python -m pytest fastQA/tests/test_documents_storage.py fastQA/tests/test_documents.py fastQA/tests/test_context_loading.py -q
```

- [ ] **Step 3: Implement MinIO-only paper behavior**

In `paper_storage.py`:

- Keep DOI normalization and object naming.
- Use `QA_ORIGINAL_MINIO_ONLY` for paper/original strict mode; do not introduce a separate fastQA paper flag.
- In strict mode, remove local candidate path checks from existence/materialization.
- `paper_exists()` should stat MinIO object only.
- `ensure_local_paper_pdf()` should materialize from MinIO object into scratch/cache path.
- If MinIO object is missing, return `None`; never mirror local file back into MinIO in strict mode.

- [ ] **Step 4: Run paper/document tests**

Run:

```bash
conda run -n agent python -m pytest fastQA/tests/test_documents_storage.py fastQA/tests/test_documents.py fastQA/tests/test_context_loading.py fastQA/tests/test_documents_view_pdf.py -q
```

Expected: all selected tests pass.

---

## Task 4: Public-Service Upload Contract Guarantees MinIO `storage_ref`

**Files:**
- Modify: `public-service/backend/app/modules/uploads/api.py`
- Modify if needed: `public-service/backend/app/modules/conversation/service.py`
- Test: `public-service/backend/tests/test_uploads_module.py`

- [ ] **Step 1: Write failing upload contract tests**

Update existing upload tests that currently allow `local://...` refs:

```python
def test_upload_pdf_success_requires_minio_storage_ref(monkeypatch):
    monkeypatch.setattr(storage_service, "mirror_file", lambda **kwargs: "local://mirrored")

    response = client.post(
        "/api/v1/upload_pdf",
        files={"file": ("sample.pdf", b"pdf-data", "application/pdf")},
        data={"conversation_id": "12"},
    )

    assert response.status_code == 503
    assert response.json()["code"] == "UPLOAD_STORAGE_UNAVAILABLE"
```

```python
def test_upload_pdf_success_persists_minio_storage_ref(monkeypatch):
    captured = {}
    monkeypatch.setattr(storage_service, "mirror_file", lambda **kwargs: "minio://agentcode/uploads/sample.pdf")
    monkeypatch.setattr(
        conversation_service_module.conversation_service,
        "add_uploaded_file",
        lambda **kwargs: captured.update(kwargs) or {"success": True, "data": {"file_id": 8}},
    )

    response = client.post(...)

    assert response.status_code == 200
    assert response.json()["storage_ref"] == "minio://agentcode/uploads/sample.pdf"
    assert captured["storage_ref"] == "minio://agentcode/uploads/sample.pdf"
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
conda run -n agent python -m pytest public-service/backend/tests/test_uploads_module.py -q
```

Expected: tests fail because local refs are still accepted by test doubles or persisted contract.

- [ ] **Step 3: Enforce MinIO ref after mirror**

In `public-service/backend/app/modules/uploads/api.py`:

- Treat `storage_service.mirror_file()` success as valid only when it returns `minio://...`.
- Reject empty, `local://...`, or bare local refs with `UPLOAD_STORAGE_UNAVAILABLE`.
- Keep local temp file cleanup behavior on rejection.
- Keep response payload `storage_ref` as the MinIO ref.

- [ ] **Step 4: Verify persistence payload**

In upload persistence flow:

- Ensure `add_uploaded_file()` receives stable `storage_ref`.
- Do not strip or replace `storage_ref` with `local_path`.
- Keep `filepath` only as local upload processing metadata.

- [ ] **Step 5: Run upload tests**

Run:

```bash
conda run -n agent python -m pytest public-service/backend/tests/test_uploads_module.py -q
```

Expected: upload tests pass and no test asserts `local://...` as a successful storage ref.

---

## Task 5: Gateway/Public-Service Storage Contract Enforcement

**Files:**
- Modify: `gateway/app/models/files.py`
- Modify: `gateway/app/services/file_context_resolver.py`
- Modify: `public-service/backend/app/modules/documents/schemas.py`
- Modify: `public-service/backend/app/modules/documents/patent_original_store.py`
- Test: `gateway/tests/test_file_context_resolver.py`
- Test: `public-service/backend/tests/test_patent_original_view_module.py`

- [ ] **Step 1: Write failing gateway tests**

Add tests:

```python
def test_gateway_rejects_local_only_execution_file_when_minio_only_enabled(monkeypatch):
    monkeypatch.setenv("QA_ORIGINAL_MINIO_ONLY", "true")
    row = ConversationFileRow(file_id=1, file_type="pdf", local_path="/tmp/a.pdf", storage_ref="")

    decision = resolver.resolve(..., file_rows=[row])

    assert decision.requires_clarification is True
    assert "storage_ref_missing" in decision.reason_codes
```

```python
def test_gateway_keeps_minio_backed_file_without_local_path(monkeypatch):
    monkeypatch.setenv("QA_ORIGINAL_MINIO_ONLY", "true")
    row = ConversationFileRow(file_id=1, file_type="pdf", local_path="", storage_ref="minio://agentcode/uploads/a.pdf")

    decision = resolver.resolve(..., file_rows=[row])

    assert decision.execution_files[0]["storage_ref"].startswith("minio://")
```

- [ ] **Step 2: Write failing public-service manifest tests**

Use existing fixture pattern:

```python
def test_patent_manifest_accepts_tables_availability_false_without_object(fake_store):
    manifest = load_manifest({"availability": {"tables": False}, "objects": {"structured": {}}})
    assert manifest.availability.tables is False
```

```python
def test_patent_original_store_table_missing_semantics(fake_store):
    # original view remains unavailable only for requested original sections;
    # table supplement code treats missing tables as no enhancement.
```

- [ ] **Step 3: Run gateway/public-service tests and verify failure**

Run:

```bash
conda run -n agent python -m pytest gateway/tests/test_file_context_resolver.py public-service/backend/tests/test_patent_original_view_module.py -q
```

- [ ] **Step 4: Implement gateway strict storage checks**

In `ConversationFileRow`:

```python
@property
def has_minio_storage_ref(self) -> bool:
    return str(self.storage_ref or "").strip().startswith("minio://")
```

In resolver:

- Before building executable file payload, count:
  - `missing_storage_ref_count`
  - `minio_storage_ref_count`
  - `local_only_file_count`
- In strict mode, selected local-only file should produce clarification/protocol failure instead of executable payload.
- Keep `local_path` in payload only as historical metadata.

- [ ] **Step 5: Implement public-service schema compatibility**

In documents schemas/store:

- Ensure `objects.structured.tables` is optional.
- Ensure `availability.tables` defaults to `False` when absent.
- Do not make tables mandatory for original view sections.

- [ ] **Step 6: Run tests**

Run:

```bash
conda run -n agent python -m pytest gateway/tests/test_file_context_resolver.py public-service/backend/tests/test_patent_original_view_module.py -q
```

Expected: selected tests pass.

---

## Task 6: Patent Original MinIO Loader For KB Tables

**Files:**
- Create: `patent/server/patent/object_reader.py`
- Create: `patent/server/patent/original_minio_loader.py`
- Modify: `patent/server/patent/runtime.py`
- Modify: `patent/server/patent/archive_loader.py`
- Test: `patent/tests/test_patent_retrieval_service.py`
- Test: `patent/tests/test_patent_stage3_evidence_loading.py`
- Create or modify: `patent/tests/test_patent_original_minio_loader.py`

- [ ] **Step 1: Write failing loader tests**

Create tests:

```python
def test_original_minio_loader_loads_tables_from_manifest(fake_minio):
    fake_minio.put_json("agentcode", "patent/originals/CN1/manifest.json", {
        "canonical_patent_id": "CN1",
        "original_version": "v1",
        "objects": {"structured": {"tables": "patent/originals/CN1/structured/tables.json"}},
        "availability": {"tables": True},
    })
    fake_minio.put_json("agentcode", "patent/originals/CN1/structured/tables.json", [
        {"table_title": "T1", "columns": ["capacity"], "rows": [{"capacity": "150"}]}
    ])

    tables = PatentOriginalMinioLoader(reader=reader).load_tables("CN1")

    assert tables[0].table_title == "T1"
    assert tables[0].rows == [{"capacity": "150"}]
```

```python
@pytest.mark.parametrize("manifest", [
    None,
    {"availability": {"tables": False}, "objects": {"structured": {}}},
])
def test_original_minio_loader_missing_manifest_or_tables_returns_empty(fake_minio, manifest):
    tables = PatentOriginalMinioLoader(reader=reader).load_tables("CN1")
    assert tables == []
```

```python
def test_original_minio_loader_does_not_read_local_tables(tmp_path, fake_minio):
    (tmp_path / "CN1_tables.json").write_text('[{"rows": [{"x": "local"}]}]', encoding="utf-8")
    tables = PatentOriginalMinioLoader(reader=reader, archive_root=tmp_path).load_tables("CN1")
    assert tables == []
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
conda run -n agent python -m pytest patent/tests/test_patent_original_minio_loader.py patent/tests/test_patent_stage3_evidence_loading.py patent/tests/test_patent_retrieval_service.py -q
```

- [ ] **Step 3: Implement `PatentOriginalMinioLoader`**

Behavior:

- Manifest object: `patent/originals/{CANONICAL}/manifest.json`.
- Manifest missing/invalid: return `[]`, record diagnostic `original_manifest_unavailable`.
- `availability.tables=false` or missing object path: return `[]`, diagnostic `tables_unavailable`.
- Object missing/download failure/JSON parse failure: return `[]`, diagnostic `tables_object_unavailable`.
- Convert table JSON to existing `PatentTableSupplement`.
- Skip empty row tables, matching existing local loader behavior.

- [ ] **Step 4: Wire runtime**

In `patent/server/patent/runtime.py`:

- Build local archive loader for catalog records as today.
- Build `PatentOriginalMinioLoader` when `PATENT_ORIGINAL_MINIO_ONLY=true`.
- Pass MinIO loader `load_tables` as `table_loader` into retrieval/evidence services.
- Do not use `archive_loader.load_tables` in strict mode.

- [ ] **Step 5: Run patent retrieval/evidence tests**

Run:

```bash
conda run -n agent python -m pytest patent/tests/test_patent_original_minio_loader.py patent/tests/test_patent_stage3_evidence_loading.py patent/tests/test_patent_retrieval_service.py -q
```

Expected: all selected tests pass.

---

## Task 7: Patent Uploaded File QA Uses `storage_ref`

**Files:**
- Modify: `patent/server/patent/file_contract.py`
- Modify: `patent/server/patent/tabular/workbook_loader.py`
- Modify: `patent/server/patent/tabular_service.py`
- Test: `patent/tests/test_patent_file_contract.py`
- Test: `patent/tests/test_patent_tabular_service.py`

- [ ] **Step 1: Write failing contract tests**

Add or create:

```python
def test_patent_file_contract_rejects_local_only_table(tmp_path):
    local = tmp_path / "a.xlsx"
    local.write_bytes(build_xlsx_bytes())

    with pytest.raises(ValueError, match="storage_ref"):
        build_patent_file_contract(
            route="tabular_qa",
            source_scope="table",
            selected_file_ids=[1],
            primary_file_id=1,
            execution_files=[{"file_id": 1, "file_type": "excel", "local_path": str(local), "storage_ref": ""}],
            ...
        )
```

```python
def test_patent_tabular_service_reads_table_from_minio_ref(fake_minio):
    # selected execution file has storage_ref and no local_path
    # answer context includes extracted table text
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
conda run -n agent python -m pytest patent/tests/test_patent_file_contract.py patent/tests/test_patent_tabular_service.py -q
```

- [ ] **Step 3: Update contract validation**

In `file_contract.py`:

- Require `storage_ref.startswith("minio://")`.
- Reject `local://` and empty ref.
- Determine suffix from `file_name` or storage object name, not `local_path`.
- Keep `local_path` in payload only as metadata.

- [ ] **Step 4: Update patent tabular workbook loading**

In `tabular/workbook_loader.py`:

- Add wrapper `load_workbook_from_execution_file(item, reader, max_sheets)`.
- For CSV, read bytes and parse with encoding loop.
- For XLS/XLSX/XLSM, materialize scratch with suffix and reuse existing path parser.
- Cache by storage object stat.

In `tabular_service.py`:

- `_load_table_descriptors()` uses wrapper and no direct `Path(local_path)`.
- `_load_table_text()` uses wrapper or MinIO scratch path only.
- If storage object unavailable, mark `_skip_file_route_cache=True` and return readable error.

- [ ] **Step 5: Run patent file tests**

Run:

```bash
conda run -n agent python -m pytest patent/tests/test_patent_file_contract.py patent/tests/test_patent_tabular_service.py patent/tests/test_patent_stage4_synthesis.py -q
```

Expected: all selected tests pass.

---

## Task 8: highThinkingQA Legacy Document Paths Use MinIO Scratch

**Files:**
- Create: `highThinkingQA/server/storage/object_reader.py`
- Modify: `highThinkingQA/server/storage/paper_storage.py`
- Modify: `highThinkingQA/server/services/documents_service.py`
- Test: `highThinkingQA/tests/test_documents_service.py`

- [ ] **Step 1: Write failing tests**

Add tests:

```python
def test_documents_service_ignores_local_pdf_when_minio_missing(monkeypatch, tmp_path):
    (tmp_path / "10.1000_demo.pdf").write_bytes(b"%PDF-local")
    monkeypatch.setenv("HIGHTHINKING_ORIGINAL_MINIO_ONLY", "true")
    monkeypatch.setattr(paper_storage, "_build_minio_client_from_env", lambda: fake_missing_minio())

    result = service.view_pdf("10.1000/demo")

    assert result.status_code == 404
```

```python
def test_documents_service_materializes_pdf_from_minio(monkeypatch, tmp_path):
    monkeypatch.setenv("HIGHTHINKING_ORIGINAL_MINIO_ONLY", "true")
    monkeypatch.setattr(paper_storage, "_build_minio_client_from_env", lambda: fake_minio_with_pdf())

    result = service.view_pdf("10.1000/demo")

    assert result.body.startswith(b"%PDF-minio")
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
conda run -n agent python -m pytest highThinkingQA/tests/test_documents_service.py -q
```

- [ ] **Step 3: Implement highThinking object reader and paper storage changes**

Behavior:

- Strict mode ignores local paper files.
- MinIO missing returns unavailable, not local fallback.
- Scratch path lives under existing highThinking runtime/cache root.
- Keep old local behavior only behind explicit rollback flag.

- [ ] **Step 4: Run tests**

Run:

```bash
conda run -n agent python -m pytest highThinkingQA/tests/test_documents_service.py -q
```

Expected: selected tests pass.

---

## Task 9: Diagnostics And Metrics For MinIO-only Reads

**Files:**
- Modify: `fastQA/app/modules/storage/object_reader.py`
- Modify: `fastQA/app/modules/storage/upload_materializer.py`
- Modify: `gateway/app/services/file_context_resolver.py`
- Modify: `public-service/backend/app/modules/documents/patent_original_store.py`
- Modify if needed: `public-service/backend/app/modules/storage/service.py`
- Modify: `patent/server/patent/object_reader.py`
- Modify: `patent/server/patent/original_minio_loader.py`
- Modify: `highThinkingQA/server/storage/object_reader.py`
- Test: `fastQA/tests/test_upload_materializer.py`
- Test: `gateway/tests/test_file_context_resolver.py`
- Test: `public-service/backend/tests/test_patent_original_view_module.py`
- Test: `patent/tests/test_patent_original_minio_loader.py`
- Test: `highThinkingQA/tests/test_documents_service.py`

- [ ] **Step 1: Write failing diagnostics tests**

Add test assertions for required counters/log events:

```python
def test_object_reader_records_read_failure_metric(fake_metrics, fake_minio):
    reader = ObjectReader(client=fake_minio, metrics=fake_metrics, runtime_root=tmp_path)

    with pytest.raises(ObjectReaderUnavailableError):
        reader.read_bytes("minio://agentcode/uploads/missing.pdf")

    assert fake_metrics.count("qa_original_minio_read_failed_total", source_family="upload_pdf") == 1
```

```python
def test_gateway_records_storage_ref_missing_metric(fake_metrics):
    # local-only selected file in strict mode
    assert fake_metrics.count("qa_original_storage_ref_missing_total") == 1
```

```python
def test_patent_loader_records_named_table_diagnostics(fake_metrics):
    loader.load_tables("CN1")
    assert fake_metrics.count("patent_tables_minio_missing_total", reason="original_manifest_unavailable") == 1
    assert fake_metrics.count("patent_tables_minio_missing_total", reason="tables_unavailable") == 0
    assert fake_metrics.count("patent_tables_minio_missing_total", reason="tables_object_unavailable") == 0
```

```python
def test_public_service_patent_original_store_records_manifest_read_metric(fake_metrics, fake_backend):
    store = PatentOriginalStore(backend=fake_backend, metrics=fake_metrics)

    store.load_manifest("CN1")

    assert fake_metrics.count(
        "qa_original_minio_read_total",
        service="public-service",
        source_family="patent_structured",
        result="success",
    ) == 1
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
conda run -n agent python -m pytest \
  fastQA/tests/test_upload_materializer.py \
  gateway/tests/test_file_context_resolver.py \
  public-service/backend/tests/test_patent_original_view_module.py \
  patent/tests/test_patent_original_minio_loader.py \
  highThinkingQA/tests/test_documents_service.py \
  -q
```

- [ ] **Step 3: Implement service-local metric hooks**

Use the lightest existing pattern in each service:

- If a metrics service exists, increment named counters.
- If no metrics service exists, add an injectable recorder/callback defaulting to no-op and log structured fields.
- Keep labels bounded:
  - `service`
  - `source_family`
  - `result`
  - `reason`

Public-service requirement:

- Instrument `PatentOriginalStore.load_manifest()`, `_load_structured_object()`, and `_stat_object()`.
- Use `service="public-service"` and source families `patent_structured`, `patent_table`, or `patent_fulltext` depending on the object being read.
- Keep the existing MinIO/local backend abstraction intact for tests, but metrics should reflect object-read success/failure.

Required metric names:

- `qa_original_minio_read_total`
- `qa_original_minio_read_failed_total`
- `qa_original_local_fallback_attempt_total`
- `qa_original_scratch_materialize_total`
- `qa_original_storage_ref_missing_total`
- `patent_tables_minio_loaded_total`
- `patent_tables_minio_missing_total`

- [ ] **Step 4: Add local fallback attempt guard**

In strict target paths:

- Increment `qa_original_local_fallback_attempt_total` if code reaches a legacy fallback branch.
- Tests should assert the metric is `0` on successful MinIO-only paths.
- Legacy fallback branches remain rollback-only and must be clearly guarded by strict-mode flags.

- [ ] **Step 5: Run diagnostics tests**

Run:

```bash
conda run -n agent python -m pytest \
  fastQA/tests/test_upload_materializer.py \
  gateway/tests/test_file_context_resolver.py \
  public-service/backend/tests/test_patent_original_view_module.py \
  patent/tests/test_patent_original_minio_loader.py \
  highThinkingQA/tests/test_documents_service.py \
  -q
```

Expected: diagnostics tests pass, including explicit patent reasons `original_manifest_unavailable`, `tables_unavailable`, and `tables_object_unavailable`.

---

## Task 10: End-to-End Verification And Cleanup

**Files:**
- Modify only files touched in Tasks 1-9.
- No deploy changes.

- [ ] **Step 1: Run service-focused test suites**

Run:

```bash
conda run -n agent python -m pytest \
  gateway/tests/test_file_context_resolver.py \
  public-service/backend/tests/test_uploads_module.py \
  public-service/backend/tests/test_patent_original_view_module.py \
  fastQA/tests/test_upload_materializer.py \
  fastQA/tests/test_workbook_loader_storage_ref.py \
  fastQA/tests/test_file_routes_materialization.py \
  fastQA/tests/test_file_routes_tabular_kb.py \
  fastQA/tests/test_documents_storage.py \
  patent/tests/test_patent_original_minio_loader.py \
  patent/tests/test_patent_retrieval_service.py \
  patent/tests/test_patent_stage3_evidence_loading.py \
  patent/tests/test_patent_file_contract.py \
  patent/tests/test_patent_tabular_service.py \
  highThinkingQA/tests/test_documents_service.py \
  -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run existing frontend tests only if gateway payload shape changes**

Run:

```bash
cd frontend-vue && npm test
```

Expected: all frontend tests pass.

- [ ] **Step 3: Run no-local-fallback source scan**

Run:

```bash
rg -n "local_path|find_local_paper_pdf|ensure_local_paper_pdf|_tables\\.json|Path\\(local_path\\)" \
  fastQA/app patent/server highThinkingQA/server gateway/app public-service/backend/app
```

Expected:

- Remaining `local_path` uses are metadata, cleanup, rollback-only, or tests.
- No strict-mode execution path reads local original/table content.

- [ ] **Step 4: Verify docs**

Run:

```bash
git diff --check -- docs/2026-05-18-qa-original-content-minio-only-spec.md docs/superpowers/plans/2026-05-18-qa-original-content-minio-only-implementation.md
```

Expected: no whitespace errors.

- [ ] **Step 5: Final worktree checkpoint**

Run:

```bash
git status --short
```

Expected:

- Implementation files changed according to the task list.
- Existing `deploy/` changes remain untouched unless the user separately asks.
- No commit is created unless the user explicitly asks.

---

## Rollback Guardrails

- Rollback flags may keep legacy code callable, but strict target tests must not depend on fallback.
- Any legacy fallback execution must increment/log `qa_original_local_fallback_attempt_total`.
- Rollback path must never upload local old files to repair MinIO silently.
- Rollback path must be documented as temporary and removed after production shadow diagnostics are stable.

## Review Checklist

Before implementation begins, confirm:

- Spec review is approved.
- This plan review is approved.
- The user has accepted the implementation scope.
- Tests are run with `conda run -n agent`.
- Commands requiring Git index/history changes are not run unless the user asks.
