# fastQA Graph Query Patterns

> Status: draft query patterns from read-only exploration. These are schema notes, not production query contracts.

## Purpose

This document collects graph query patterns that can support a four-route KB experience in fastQA:

- `precise`
- `semantic`
- `hybrid`
- `community`

The patterns target the currently populated field-bucket Neo4j schema.

## Common Guardrails

All graph execution intended for user questions should apply these guardrails:

- read-only Cypher only
- mandatory `LIMIT`
- timeout enforcement
- label and relationship allowlist
- DOI syntax validation before direct answer rendering
- suspicious DOI filtering before graph-seeded RAG
- original text preservation for any parsed numeric values
- place filtering `WHERE` clauses on the mandatory `MATCH`/`WITH` that owns the value being filtered; avoid accidentally attaching a filter only to a later `OPTIONAL MATCH`

Recommended initial suspicious DOI signals:

- DOI does not match a strict DOI pattern.
- DOI has no title and no useful evidence rows.
- DOI has unusually many titles.
- DOI has obviously truncated publisher prefix patterns such as `10.1007/s12598-`.
- DOI contains non-DOI text corruption.

## DOI Lookup

Use for:

- direct DOI questions
- graph context expansion for a DOI
- seeding RAG with DOI-specific facts

Basic lookup:

```cypher
MATCH (d:doi {name: $doi})
OPTIONAL MATCH (d)-[:title]->(t:title)
RETURN d.name AS doi, collect(DISTINCT t.name)[0..5] AS titles
LIMIT 1
```

One-hop expansion:

```cypher
MATCH (d:doi {name: $doi})-[r]->(x)
RETURN
  type(r) AS rel,
  labels(x) AS labels,
  x.name AS name,
  x.louvainCommunityId AS community
LIMIT 80
```

Two-hop bucket expansion:

```cypher
MATCH (d:doi {name: $doi})-[r1]->(bucket)-[r2]->(value)
RETURN
  type(r1) AS bucket_rel,
  labels(bucket) AS bucket_labels,
  bucket.name AS bucket,
  type(r2) AS value_rel,
  labels(value) AS value_labels,
  value.name AS value
LIMIT 120
```

## Material And Title Search

Use for:

- `precise` literature listing
- `hybrid` DOI candidate generation
- broad graph evidence before vector search

Title search:

```cypher
MATCH (d:doi)-[:title]->(t:title)
WHERE toLower(t.name) CONTAINS toLower($term)
RETURN d.name AS doi, t.name AS title
LIMIT 50
```

Sample/material search:

```cypher
MATCH (d:doi)-[:name]->(m:name)
WHERE toLower(m.name) CONTAINS toLower($term)
OPTIONAL MATCH (d)-[:title]->(t:title)
RETURN d.name AS doi, t.name AS title, m.name AS material
LIMIT 50
```

Raw material search:

```cypher
MATCH (d:doi)-[:raw_materials]->(:raw_materials)-[:raw_materials]->(rm:raw_materials)
WHERE toLower(rm.name) CONTAINS toLower($term)
OPTIONAL MATCH (d)-[:title]->(t:title)
RETURN
  rm.name AS raw_material,
  count(DISTINCT d) AS doi_count,
  collect(DISTINCT d.name)[0..8] AS dois,
  collect(DISTINCT t.name)[0..3] AS titles
ORDER BY doi_count DESC
LIMIT 20
```

## Precise Property Query Patterns

Use for:

- ranking
- threshold filtering
- field-specific comparison
- count/list by structured property

Density context:

```cypher
MATCH (m:name)-[:compaction_density]->(cd:compaction_density)
MATCH (d:doi)-[:name]->(m)
OPTIONAL MATCH (d)-[:title]->(t:title)
RETURN
  d.name AS doi,
  t.name AS title,
  m.name AS material,
  cd.name AS compaction_density
LIMIT 50
```

Tap density context:

```cypher
MATCH (m:name)-[:tap_density]->(td:tap_density)
MATCH (d:doi)-[:name]->(m)
OPTIONAL MATCH (d)-[:title]->(t:title)
RETURN
  d.name AS doi,
  t.name AS title,
  m.name AS material,
  td.name AS tap_density
LIMIT 50
```

Discharge capacity requires two patterns because the graph has both direct values and placeholder parent nodes.

Direct capacity:

```cypher
MATCH (m:name)-[:discharge_capacity]->(cap:discharge_capacity)
WHERE NOT cap.name STARTS WITH "discharge_capacity"
MATCH (d:doi)-[:name]->(m)
OPTIONAL MATCH (d)-[:title]->(t:title)
RETURN d.name AS doi, t.name AS title, m.name AS material, cap.name AS capacity
LIMIT 50
```

Placeholder parent to child capacity:

```cypher
MATCH (d:doi)-[:name]->(m:name)-[:discharge_capacity]->(parent:discharge_capacity)-[:discharge_capacity]->(cap:discharge_capacity)
OPTIONAL MATCH (d)-[:title]->(t:title)
RETURN d.name AS doi, t.name AS title, m.name AS material, parent.name AS capacity_group, cap.name AS capacity
LIMIT 50
```

Title-constrained two-hop capacity:

```cypher
MATCH (d:doi)-[:title]->(t:title)
WHERE toLower(t.name) CONTAINS toLower($term)
MATCH (d)-[:name]->(m:name)-[:discharge_capacity]->(parent:discharge_capacity)-[:discharge_capacity]->(cap:discharge_capacity)
RETURN d.name AS doi, t.name AS title, m.name AS material, parent.name AS capacity_group, cap.name AS capacity
LIMIT 50
```

Production precise queries should parse numeric values in application code rather than trying to encode all unit handling in Cypher.

## Process And Recipe Patterns

Preparation methods:

```cypher
MATCH (d:doi)-[:process]->(:process)-[:preparation_method]->(pm:preparation_method)
WHERE toLower(pm.name) CONTAINS toLower($term)
OPTIONAL MATCH (d)-[:title]->(t:title)
RETURN d.name AS doi, t.name AS title, pm.name AS method
LIMIT 50
```

Key process parameters:

```cypher
MATCH (d:doi)-[:process]->(:process)-[:key_process_parameters]->(k:key_process_parameters)-[r]->(v)
OPTIONAL MATCH (d)-[:title]->(t:title)
RETURN
  d.name AS doi,
  t.name AS title,
  type(r) AS parameter_type,
  labels(v) AS value_labels,
  v.name AS value
LIMIT 80
```

Key process parameter child fields are not usually direct `temperature` / `time` / `pressure` children in the inspected sample. Useful values are mainly encoded under operation-specific child labels.

Calcination parameters:

```cypher
MATCH (d:doi)-[:process]->(:process)-[:key_process_parameters]->(:key_process_parameters)-[:calcination]->(v:calcination)
OPTIONAL MATCH (d)-[:title]->(t:title)
RETURN d.name AS doi, t.name AS title, v.name AS calcination
LIMIT 50
```

Milling parameters:

```cypher
MATCH (d:doi)-[:process]->(:process)-[:key_process_parameters]->(:key_process_parameters)-[:milling]->(v:milling)
OPTIONAL MATCH (d)-[:title]->(t:title)
RETURN d.name AS doi, t.name AS title, v.name AS milling
LIMIT 50
```

Sintering and drying parameters:

```cypher
MATCH (d:doi)-[:process]->(:process)-[:key_process_parameters]->(:key_process_parameters)-[r]->(v)
OPTIONAL MATCH (d)-[:title]->(t:title)
WHERE type(r) IN ["sintering", "drying"]
RETURN d.name AS doi, t.name AS title, type(r) AS process_field, v.name AS value
LIMIT 80
```

Recipe fields:

```cypher
MATCH (d:doi)-[:recipe]->(:recipe)-[r]->(v)
OPTIONAL MATCH (d)-[:title]->(t:title)
WHERE type(r) IN ["carbon_source", "carbon_content", "additives", "dopant", "doping_elements", "Li_Fe_ratio", "Fe_P_ratio", "other_ratios"]
RETURN
  d.name AS doi,
  t.name AS title,
  type(r) AS recipe_field,
  labels(v) AS value_labels,
  v.name AS value
LIMIT 80
```

Carbon source listing:

```cypher
MATCH (d:doi)-[:recipe]->(:recipe)-[:carbon_source]->(cs:carbon_source)
WHERE toLower(cs.name) CONTAINS toLower($term)
OPTIONAL MATCH (d)-[:title]->(t:title)
RETURN
  cs.name AS carbon_source,
  count(DISTINCT d) AS doi_count,
  collect(DISTINCT d.name)[0..8] AS dois,
  collect(DISTINCT t.name)[0..5] AS titles
ORDER BY doi_count DESC
LIMIT 20
```

Dopant and doping element context:

```cypher
MATCH (d:doi)-[:recipe]->(:recipe)-[r]->(v)
WHERE type(r) IN ["dopant", "doping_elements"]
  AND toLower(v.name) CONTAINS toLower($term)
OPTIONAL MATCH (d)-[:title]->(t:title)
RETURN
  d.name AS doi,
  t.name AS title,
  type(r) AS field,
  v.name AS value
LIMIT 50
```

Application code should parse serialized dictionary-like values in `additives` and `doping_elements` before rendering them.

## Community Patterns

Use for:

- relationship/network questions
- mechanism association questions
- same-cluster literature discovery
- community summaries

Find communities relevant to a title/material term:

```cypher
MATCH (d:doi)-[:title]->(t:title)
WHERE toLower(t.name) CONTAINS toLower($term)
WITH d.louvainCommunityId AS cid,
     count(DISTINCT d) AS doi_count,
     collect(DISTINCT d.name)[0..8] AS dois,
     collect(DISTINCT t.name)[0..5] AS titles
RETURN cid, doi_count, dois, titles
ORDER BY doi_count DESC
LIMIT 12
```

Community label profile:

```cypher
MATCH (n {louvainCommunityId: $cid})
UNWIND labels(n) AS label
RETURN label, count(*) AS count
ORDER BY count DESC
LIMIT 40
```

Community DOI/title representatives:

```cypher
MATCH (d:doi {louvainCommunityId: $cid})
OPTIONAL MATCH (d)-[:title]->(t:title)
RETURN d.name AS doi, t.name AS title
LIMIT 20
```

Community process method representatives:

```cypher
MATCH (d:doi {louvainCommunityId: $cid})-[:process]->(:process)-[:preparation_method]->(pm:preparation_method)
RETURN
  pm.name AS method,
  count(DISTINCT d) AS doi_count,
  collect(DISTINCT d.name)[0..5] AS dois
ORDER BY doi_count DESC
LIMIT 20
```

Community raw material representatives:

```cypher
MATCH (d:doi {louvainCommunityId: $cid})-[:raw_materials]->(:raw_materials)-[:raw_materials]->(rm:raw_materials)
RETURN rm.name AS raw_material, count(DISTINCT d) AS doi_count
ORDER BY doi_count DESC
LIMIT 25
```

Community material/sample representatives:

```cypher
MATCH (d:doi {louvainCommunityId: $cid})-[:name]->(m:name)
RETURN
  m.name AS material,
  count(DISTINCT d) AS doi_count,
  collect(DISTINCT d.name)[0..5] AS dois
ORDER BY doi_count DESC
LIMIT 25
```

Community performance profile:

```cypher
MATCH (d:doi {louvainCommunityId: $cid})-[:name]->(:name)-[r]->(v)
WHERE type(r) IN [
  "discharge_capacity",
  "cycling_stability",
  "conductivity",
  "coulombic_efficiency",
  "particle_size",
  "surface_area",
  "tap_density",
  "compaction_density"
]
RETURN
  type(r) AS field,
  count(*) AS value_count,
  collect(DISTINCT v.name)[0..8] AS examples
ORDER BY value_count DESC
LIMIT 20
```

Community answer assembly should:

- find candidate communities by title/material/process term,
- extract representative titles, materials, methods, raw materials, and performance fields,
- assign a generated human-readable cluster label,
- use DOI candidates to fetch vector/PDF/MD evidence when the answer needs citations.

## Hybrid Pattern

Recommended first implementation:

1. Classify the question as `hybrid`.
2. Build one or more graph candidate queries from structured signals:
   - material term
   - property field
   - process/recipe field
   - DOI if present
   - community if relationship/network language is present
3. Canonicalize graph rows into:
   - DOI candidates
   - entity hints
   - fact block
   - diagnostics
4. Inject those into RAG:
   - Stage1 graph context
   - Stage2 query prefixes
   - Stage4 supplemental facts

The current `GraphRagPayload` already matches this direction, but it needs richer graph planning and field coverage.

Concrete first-pass hybrid graph seed:

```cypher
MATCH (d:doi)
OPTIONAL MATCH (d)-[:title]->(t:title)
OPTIONAL MATCH (d)-[:name]->(m:name)
OPTIONAL MATCH (d)-[:process]->(:process)-[:preparation_method]->(pm:preparation_method)
OPTIONAL MATCH (d)-[:recipe]->(:recipe)-[:carbon_source]->(cs:carbon_source)
WITH d, t,
     collect(DISTINCT m.name)[0..5] AS materials,
     collect(DISTINCT pm.name)[0..5] AS methods,
     collect(DISTINCT cs.name)[0..5] AS carbon_sources
WHERE any(term IN $query_terms WHERE
  toLower(coalesce(t.name, "")) CONTAINS term OR
  any(item IN materials WHERE toLower(coalesce(item, "")) CONTAINS term) OR
  any(item IN methods WHERE toLower(coalesce(item, "")) CONTAINS term) OR
  any(item IN carbon_sources WHERE toLower(coalesce(item, "")) CONTAINS term)
)
RETURN
  d.name AS doi,
  t.name AS title,
  materials,
  methods,
  carbon_sources
LIMIT 30
```

The resulting rows map naturally to:

- `stage2_doi_candidates`: `doi`
- `stage2_entity_hints`: `materials`, `methods`, `carbon_sources`, `title`
- `stage4_fact_block`: compact facts from the same row

## Semantic Pattern

The graph should normally not be the primary executor for pure semantic questions:

- why/how questions
- open-ended mechanisms
- broad trends
- literature synthesis
- questions requiring full evidence from papers

The graph may still provide optional hints if the question includes clear material, process, recipe, or performance entities, but final evidence should come from vector/PDF retrieval.

Current Chroma handoff notes:

- Summary/vector store metadata has direct `doi`, so graph DOI candidates can be matched directly.
- MD store metadata has `document_name` and `filename`; DOI values need slash-to-underscore normalization.
- Graph evidence should be injected as short prefixes/facts, not as a replacement for vector evidence.
