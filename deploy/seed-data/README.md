# Seed Data Layout

Put portable deployment data here before building a release bundle.

Expected subdirectories:

- `public-service/`
  - `vector_database/`
  - `papers/`
  - `storage/`
  - `translation_cache/` if you want to carry translated cache data
- `fastQA/`
  - `vector_database/`
  - `vector_database_local/`
  - `vector_database_md/`
  - `community_vector_database/`
  - `vector_db_topic_index.json`
- `highThinkingQA/`
  - `vectordb/`
  - `papers/`

Recommended collection command:

```bash
bash deploy/scripts/collect_seed_data.sh --clean
```

Default source paths used by the helper script:

- `public-service`: `public-service/data/runtime/`
- `fastQA`: prefer `resource/fastqa/`, fallback `resource/state/dev/fastQA/`
- `highThinkingQA`: prefer `resource/highThinkingQA/`, fallback `resource/state/dev/highThinkingQA/`

`fastQA` papers are intentionally excluded from `seed-data/` because the portable
deployment bundle carries them through `deploy/minio-seed/<bucket>/papers/`.
That avoids packaging the same corpus twice.

You can override the source roots with environment variables:

```bash
PUBLIC_SERVICE_SRC=/path/to/public-service/runtime \
FASTQA_SRC=/path/to/fastqa/data-root \
HIGHTHINKINGQA_SRC=/path/to/highthinkingqa/data-root \
bash deploy/scripts/collect_seed_data.sh --clean
```
