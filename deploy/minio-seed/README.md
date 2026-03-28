# MinIO Seed Layout

Put portable MinIO object seeds here before building a release bundle.

Expected layout:

- `<bucket>/`
  - `papers/`
  - other object prefixes when needed

Recommended collection command:

```bash
bash deploy/scripts/collect_minio_seed.sh agentcode --clean
```

This populates:

- `deploy/minio-seed/agentcode/papers/`

Default source directories:

- `public-service/data/runtime/papers`
- prefer `resource/highThinkingQA/papers`, fallback `resource/state/dev/highThinkingQA/papers`
- prefer `resource/fastqa/papers`, fallback `resource/state/dev/fastQA/papers`
- `resource/state/dev/fastQA/papers_local`

In the current worktree, the main corpus is under `resource/fastqa/papers`, so the
portable MinIO seed should normally be built from that directory rather than the
smaller `resource/state/dev/fastQA/papers` cache.

During deployment, `docker-compose.yml` runs the `minio-seed` one-shot container
after bucket creation and imports everything under:

- `deploy/minio-seed/<bucket>/`

into:

- `minio://<bucket>/`
