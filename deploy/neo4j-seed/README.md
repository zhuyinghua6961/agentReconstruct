# Legacy Neo4j Seed Reference

The current delivery path uses compressed Neo4j dump packages in `deploy/data/`:

- `neo4j-literature.dump.zst`
- `neo4j-patent.dump.zst`

During deployment, Compose runs:

- `neo4j-literature-prepare` with `seed-tools` to decompress the literature dump
- `neo4j-literature-seed` with the official Neo4j image to load the dump
- `neo4j-patent-prepare` with `seed-tools` to decompress the patent dump
- `neo4j-patent-seed` with the official Neo4j image to load the dump

Backends then connect internally to:

- `bolt://neo4j-literature:7687`
- `bolt://neo4j-patent:7687`

This directory is kept only as a local reference for older copied Neo4j data
experiments. Prefer consistent `neo4j-admin database dump` outputs generated
during a graph maintenance window.
