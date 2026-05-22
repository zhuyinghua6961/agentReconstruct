# MinIO Seed Staging Layout

This directory is a local staging area used before creating
`deploy/data/minio-originals.tar.zst`. It is not mounted by Compose on the
deployment machine.

Expected layout:

- `<bucket>/papers/`
- `<bucket>/patent/originals/<canonical_patent_id>/manifest.json`
- `<bucket>/patent/originals/<canonical_patent_id>/structured/*.json`
- `<bucket>/patent/originals/<canonical_patent_id>/fulltext/original.pdf`
- `<bucket>/patent/originals/<canonical_patent_id>/figures/...`

Collect from the current worktree:

```bash
bash deploy/scripts/collect_minio_seed.sh agentcode --clean
```

The current corpus should primarily come from `resource/fastqa/papers`, with
patent originals from `resource/patentQA`.

Patent originals are converted to the runtime MinIO object layout. Local
`*_tables.json` files are written as `structured/tables.json`, and each
corresponding `manifest.json` gets `objects.structured.tables` and
`availability.tables=true`.

After collection, run:

```bash
bash deploy/scripts/package_data.sh deploy/.env
```

That script packages the bucket-independent contents as
`deploy/data/minio-originals.tar.zst`, where the tar root contains `papers/` and
`patent/originals/`.

The older `build_minio_originals_image.sh` path is retained only for
legacy/debug use.
