# MinIO Seed Layout

Put portable MinIO object seeds here before building a release bundle.

Expected layout:

- `<bucket>/`
  - `papers/`
  - `patent/originals/<canonical_patent_id>/`
    - `manifest.json`
    - `structured/claims.json`
    - `structured/description.json`
    - `structured/bibliography.json`
    - `fulltext/original.pdf`
    - `figures/...`

Recommended collection command:

```bash
bash deploy/scripts/collect_minio_seed.sh agentcode --clean
```

This populates:

- `deploy/minio-seed/agentcode/papers/`
- `deploy/minio-seed/agentcode/patent/originals/`

Default source directories:

- `public-service/data/runtime/papers`
- prefer `resource/highThinkingQA/papers`, fallback `resource/state/dev/highThinkingQA/papers`
- prefer `resource/fastqa/papers`, fallback `resource/state/dev/fastQA/papers`
- `resource/state/dev/fastQA/papers_local`
- `resource/patentQA`

In the current worktree, the main corpus is under `resource/fastqa/papers`, so the
portable MinIO seed should normally be built from that directory rather than the
smaller `resource/state/dev/fastQA/papers` cache.

Patent originals are not raw-copied. The collection script uses the patent
service's original asset tooling to convert each local patent directory into the
same object structure used by runtime MinIO:

- local `著录项目.json` -> `structured/bibliography.json`
- local `权利要求.json` -> `structured/claims.json`
- local `说明书.json` -> `structured/description.json`
- local PDF -> `fulltext/original.pdf`
- local figures -> `figures/summary` and `figures/fulltext`

Useful variants:

```bash
bash deploy/scripts/collect_minio_seed.sh agentcode --patent-only
bash deploy/scripts/collect_minio_seed.sh agentcode --papers-only
PATENT_ORIGINALS_SRC=/path/to/patentQA bash deploy/scripts/collect_minio_seed.sh agentcode --patent-only
```

During deployment, `docker-compose.yml` runs the `minio-seed` one-shot container
after bucket creation and imports everything under:

- `deploy/minio-seed/<bucket>/`

into:

- `minio://<bucket>/`
