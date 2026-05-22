# Offline Data Packages

Place generated deployment data packages here before running the Docker stack.

Expected files:

- `manifest.json`
- `minio-originals.tar.zst`
- `fastqa-ref.tar.zst`
- `highthinking-ref.tar.zst`
- `patentqa-ref.tar.zst`
- `public-service-ref.tar.zst`
- `neo4j-literature.dump.zst`
- `neo4j-patent.dump.zst`

Generate the packages from the repository root with:

```bash
bash deploy/scripts/package_data.sh deploy/.env
```

Package contents are ignored by Git; this README is tracked only to preserve the
directory contract.

On the deployment machine, Compose seed jobs read these packages directly. The
host does not need `zstd`, `mc`, or `neo4j-admin`; those tools run inside
`highthinking/seed-tools` and the official Neo4j image.
