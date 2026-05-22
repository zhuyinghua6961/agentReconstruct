# Legacy Seed Data Layout

`deploy/seed-data/` was the earlier staging directory for copying reference data
directly into Compose volumes. The recommended offline delivery now packages
reference data into versioned archives under `deploy/data/`:

- `fastqa-ref.tar.zst`
- `highthinking-ref.tar.zst`
- `patentqa-ref.tar.zst`
- `public-service-ref.tar.zst`

Use the unified packager instead:

```bash
bash deploy/scripts/package_data.sh deploy/.env
```

`collect_seed_data.sh` remains available for legacy/debug workflows, but the
current `docker-compose.yml` seeds from `deploy/data/*.tar.zst`.
