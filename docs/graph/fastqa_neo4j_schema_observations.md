# fastQA Neo4j Graph Schema Observations

> Status: exploratory notes from read-only Neo4j inspection. Do not store secrets in this document.

## Purpose

This document records the current Neo4j graph shape used by `fastQA`, with emphasis on how the graph can support the intended four-route KB experience:

- `precise`: structured graph query
- `semantic`: vector/RAG query
- `hybrid`: graph-constrained vector/RAG query
- `community`: relationship/community query

The goal is to describe the current graph reality, not the older legacy code shape.

## Configuration Observations

The active fastQA graph configuration is under:

- `resource/config/services/fastQA/config.shared.env`
- `resource/config/services/fastQA/config.secret.env`

Observed non-secret behavior:

- fastQA graph KB is enabled in the service config.
- graph KB V2 is enabled in the service config.
- graph-to-RAG injection is enabled in the service config.
- Neo4j is reachable through the configured local Bolt endpoint.
- The `conda` environment named `agent` contains the Neo4j Python driver.

Secrets such as username and password are intentionally omitted.

## High-Level Schema Shape

The graph is primarily a field-bucket schema rather than a normalized domain ontology.

The dominant pattern is:

```cypher
(:doi)-[:title]->(:title)
(:doi)-[:name]->(:name)
(:doi)-[:testing]->(:testing)-[:testing]->(:testing)
(:doi)-[:recipe]->(:recipe)-[:carbon_source]->(:carbon_source)
(:doi)-[:process]->(:process)-[:preparation_method]->(:preparation_method)
(:doi)-[:equipment]->(:equipment)-[:model|parameters|structure]->(...)
(:name)-[:discharge_capacity]->(:discharge_capacity)
(:name)-[:cycling_stability]->(:cycling_stability)
(:name)-[:compaction_density]->(:compaction_density)
```

Most observed domain nodes have:

- `name`
- `louvainCommunityId`

The `louvainCommunityId` field appears broadly populated and is a practical starting point for a `community` route.

## Important Labels

Core document and bucket labels:

- `doi`
- `title`
- `name`
- `testing`
- `recipe`
- `raw_materials`
- `process`
- `equipment`
- `key_process_parameters`
- `process_steps`
- `preparation_method`

Performance and property labels:

- `discharge_capacity`
- `cycling_stability`
- `compaction_density`
- `conductivity`
- `particle_size`
- `tap_density`
- `surface_area`
- `energy_density`
- `power_density`
- `coulombic_efficiency`

Recipe and process labels:

- `carbon_source`
- `carbon_content`
- `additives`
- `dopant`
- `doping_elements`
- `Li_Fe_ratio`
- `Fe_P_ratio`
- `other_ratios`
- `temperature`
- `time`
- `pressure`
- `atmosphere`
- `calcination`
- `milling`
- `sintering`
- `drying`

There are also labels that look like a newer or separate ontology:

- `Article`
- `Entity`
- `Material`
- `Process`
- `Step`
- `Equipment`
- `__Document__`
- `__Chunk__`
- `__Entity__`

These need further inspection before being used in query planning.

Follow-up inspection shows that these newer ontology labels currently have constraints and indexes but no populated nodes in the inspected database:

| Label | Count |
| --- | ---: |
| `Article` | 0 |
| `Entity` | 0 |
| `Material` | 0 |
| `Process` | 0 |
| `Step` | 0 |
| `Equipment` | 0 |
| `__Document__` | 0 |
| `__Chunk__` | 0 |
| `__Entity__` | 0 |

Implication: short-term query planning should not depend on these labels. They can remain reserved for future ingestion or normalized ontology migration, but the current four-route implementation should target the field-bucket schema.

## Approximate Node Counts

Top observed counts:

| Label | Count |
| --- | ---: |
| `discharge_capacity` | 122,452 |
| `name` | 83,283 |
| `testing` | 63,387 |
| `raw_materials` | 55,319 |
| `description` | 51,427 |
| `parameters` | 45,989 |
| `step_name` | 44,377 |
| `reagents` | 29,463 |
| `morphology` | 26,548 |
| `conditions` | 18,057 |
| `cycling_stability` | 17,617 |
| `model` | 17,606 |
| `doi` | 14,075 |
| `structure` | 12,742 |
| `title` | 12,374 |
| `process` | 10,678 |
| `recipe` | 10,678 |
| `equipment` | 10,677 |
| `key_process_parameters` | 10,677 |
| `process_steps` | 10,676 |

This indicates that the graph has enough coverage for structured and mixed retrieval, especially around DOI, material/sample names, preparation process, equipment, testing, and electrochemical properties.

## Constraints And Indexes

The inspected database has uniqueness constraints and matching range indexes on `name` for nearly all field-bucket labels, for example:

- `doi(name)`
- `title(name)`
- `name(name)`
- `raw_materials(name)`
- `testing(name)`
- `recipe(name)`
- `process(name)`
- `equipment(name)`
- `discharge_capacity(name)`
- `cycling_stability(name)`
- `compaction_density(name)`
- `conductivity(name)`
- `particle_size(name)`
- `carbon_source(name)`
- `doping_elements(name)`

The newer ontology labels also have uniqueness constraints such as `Article(id)`, `Article(doi)`, `Material(id)`, `Material(name)`, `Process(id)`, `Process(name)`, `Equipment(id)`, and `Equipment(name)`, but those labels currently have no node data in the inspected database.

Follow-up index inspection:

| Index Type | Count |
| --- | ---: |
| `LOOKUP` | 2 |
| `RANGE` | 62 |

There are no observed Neo4j full-text indexes or Neo4j vector indexes in the inspected database. The current graph database should therefore be treated as a structured store, not as the semantic search store.

Implication: exact `name` lookups are index-backed. Substring searches using `CONTAINS` are still likely to scan label populations unless additional full-text indexes exist or are added later. For the first four-route implementation, semantic retrieval should stay in Chroma, while Neo4j should provide structured candidates, filters, and facts.

## Relationship Distribution

Top relationship counts from the inspected graph:

| Relationship | Count |
| --- | ---: |
| `discharge_capacity` | 185,439 |
| `name` | 119,571 |
| `raw_materials` | 83,162 |
| `testing` | 63,703 |
| `parameters` | 58,979 |
| `description` | 45,600 |
| `step_name` | 44,377 |
| `conditions` | 38,403 |
| `reagents` | 38,356 |
| `morphology` | 33,893 |
| `model` | 33,088 |
| `structure` | 24,338 |
| `instrument` | 22,879 |
| `temperature` | 20,101 |
| `time` | 19,951 |
| `cycling_stability` | 19,673 |
| `particle_size` | 14,853 |
| `title` | 12,269 |
| `atmosphere` | 10,829 |
| `process` / `recipe` / `equipment` / `process_steps` | about 10,675 each |

Direct DOI relationship counts:

| DOI Relationship | Count |
| --- | ---: |
| `name` | 68,127 |
| `title` | 12,269 |
| `recipe` | 10,676 |
| `raw_materials` | 10,676 |
| `process` | 10,676 |
| `testing` | 10,675 |
| `equipment` | 10,675 |

Recipe relationship counts:

| Recipe Relationship | Count |
| --- | ---: |
| `other_ratios` | 5,375 |
| `carbon_source` | 5,337 |
| `additives` | 4,769 |
| `carbon_content` | 3,734 |
| `Fe_P_ratio` | 2,325 |
| `Li_Fe_ratio` | 2,245 |
| `doping_elements` | 1,396 |
| `dopant` | 1,367 |

Process relationship counts:

| Process Relationship | Count |
| --- | ---: |
| `step_name` | 44,377 |
| `materials` | 10,678 |
| `key_process_parameters` | 10,677 |
| `process_steps` | 10,676 |
| `preparation_method` | 10,109 |

Key process parameter relationship counts:

| Parameter Relationship | Count |
| --- | ---: |
| `drying` | 9,409 |
| `other_parameters` | 9,037 |
| `calcination` | 8,436 |
| `milling` | 8,430 |
| `sintering` | 7,604 |
| `annealing` | 1 |

Implication: route planning should prefer known bucket shapes over free-form Cypher generation. The graph has strong coverage for process, recipe, equipment, test, and performance fields, but most value-bearing nodes still require normalization before clean direct answers.

## DOI Coverage

Observed DOI graph characteristics:

- `:doi` nodes: 14,075.
- `:title` nodes: 12,374.
- DOI nodes without a direct `(:doi)-[:title]->(:title)` edge: 1,948.
- DOI degree distribution:
  - min: 1
  - p50: 9
  - p90: 15
  - max: 342
  - average: about 9.5

Implication: DOI lookup can be a first-class graph capability, but answer rendering should tolerate missing titles and sparse DOI roots.

## Representative DOI Expansion

One inspected DOI expansion showed this shape:

```cypher
(:doi)-[:title]->(:title)
(:doi)-[:name]->(:name)
(:doi)-[:testing]->(:testing)
(:doi)-[:recipe]->(:recipe)
(:doi)-[:raw_materials]->(:raw_materials)
(:doi)-[:process]->(:process)
(:doi)-[:equipment]->(:equipment)
```

Two-hop expansion showed useful structured evidence:

```cypher
(:name)-[:morphology]->(:morphology)
(:name)-[:discharge_capacity]->(:discharge_capacity)
(:name)-[:cycling_stability]->(:cycling_stability)
(:testing)-[:testing]->(:testing)
(:recipe)-[:other_ratios]->(:other_ratios)
(:recipe)-[:Li_Fe_ratio]->(:Li_Fe_ratio)
(:recipe)-[:Fe_P_ratio]->(:Fe_P_ratio)
(:recipe)-[:carbon_source]->(:carbon_source)
(:raw_materials)-[:raw_materials]->(:raw_materials)
(:process)-[:step_name]->(:step_name)
(:process)-[:process_steps]->(:process_steps)
(:process)-[:preparation_method]->(:preparation_method)
(:process)-[:key_process_parameters]->(:key_process_parameters)
(:equipment)-[:structure]->(:structure)
(:equipment)-[:parameters]->(:parameters)
(:equipment)-[:name]->(:name)
```

Implication: graph direct answers can cover DOI metadata, raw materials, preparation, testing, equipment, and selected performance properties without relying on PDF text. Final citation-heavy answers should still use RAG/PDF where available.

## Initial Route Implications

### `precise`

Likely feasible using Neo4j directly.

Good candidates:

- DOI lookup
- material/title/raw material listing
- count by material or raw material
- property filters over labels such as `compaction_density`, `tap_density`, `discharge_capacity`, `conductivity`, `particle_size`
- top-N/ranking queries, provided values can be parsed from `name` strings

Main risk:

- Many values are strings with units, ranges, annotations, or placeholder node names. A numeric parser is needed before reliable sorting/filtering.

Further inspection of performance fields:

| Field Label | Total Nodes | Placeholder-Like Nodes | Direct Value Nodes | Notes |
| --- | ---: | ---: | ---: | --- |
| `discharge_capacity` | 122,452 | 66,809 | 55,643 | Mixed parent placeholder and direct value pattern. Needs two-hop expansion. |
| `cycling_stability` | 17,617 | 0 | 17,617 | Mostly direct text values. |
| `conductivity` | 8,352 | 1 | 8,351 | Mostly direct values, varied units. |
| `coulombic_efficiency` | 7,078 | 0 | 7,078 | Direct values, sometimes percentages or fractions. |
| `particle_size` | 7,073 | 0 | 7,073 | Direct values, often ranges or distributions. |
| `surface_area` | 3,485 | 0 | 3,485 | Direct values, varied units. |
| `energy_density` | 1,463 | 0 | 1,463 | Direct values, varied units. |
| `tap_density` | 530 | 0 | 530 | Direct values. |
| `power_density` | 463 | 0 | 463 | Direct values, often multiple values in one string. |
| `compaction_density` | 162 | 0 | 162 | Direct values, good candidate for first precise numeric route. |

Observed performance relationship shapes:

```cypher
(:name)-[:compaction_density]->(:compaction_density)
(:name)-[:conductivity]->(:conductivity)
(:name)-[:coulombic_efficiency]->(:coulombic_efficiency)
(:name)-[:cycling_stability]->(:cycling_stability)
(:name)-[:discharge_capacity]->(:discharge_capacity)
(:discharge_capacity)-[:discharge_capacity]->(:discharge_capacity)
(:name)-[:energy_density]->(:energy_density)
(:name)-[:particle_size]->(:particle_size)
(:name)-[:power_density]->(:power_density)
(:name)-[:surface_area]->(:surface_area)
(:name)-[:tap_density]->(:tap_density)
```

Example direct values:

- `compaction_density`: `2.446 g/cm³`, `3.19 g/cm³ at 250 MPa loading`, `65% of theoretical value`
- `tap_density`: `1.90 g cm⁻³`, `1.27 g cm⁻³`
- `conductivity`: `2.3 × 10⁻³ S cm⁻¹`, `3.3 × 10^{-5} S cm^{-1} at RT`
- `cycling_stability`: `98.5% capacity retention after 500 cycles at 0.2 A g⁻¹`
- `discharge_capacity`: `0.5C_initial_141.2 mA h g⁻¹`, `0.2A/g_initial_1286.1 mA h g⁻¹`
- `coulombic_efficiency`: `94-96% (second cycle)`, `over 99% at 0.5C over 150 cycles`
- `surface_area`: `41.60 m² g⁻¹`, `16-19 m²/g`
- `particle_size`: `30–40 μm`, `D50 ~ 1.2 μm after crushing`
- `energy_density`: `500 Wh kg⁻¹ at 56 W kg⁻¹, 200 Wh kg⁻¹ at 1400 W kg⁻¹`
- `power_density`: `56 W kg⁻¹ to 1400 W kg⁻¹`

Implication: the first robust `precise` implementation should start with a small set of fields and explicit parsers:

- density parser for `compaction_density` and `tap_density`
- capacity parser for `discharge_capacity`
- retention/cycle parser for `cycling_stability`
- conductivity parser for `conductivity`

Each parser should preserve the original text alongside parsed numeric values.

### `semantic`

Should continue to use Chroma/RAG as the main route.

Good candidates:

- mechanisms
- broad summaries
- trends
- why/how questions
- questions requiring full-text evidence beyond structured fields

### `hybrid`

Feasible and probably the most valuable route.

Likely shape:

1. Use Neo4j to identify DOI candidates, material candidates, and structured graph facts.
2. Inject DOI/entity/fact hints into Stage1/Stage2/Stage4 RAG.
3. Use PDF/vector evidence as the final citation-bearing evidence.

This aligns with the current `GraphRagPayload` direction but needs richer graph planning.

### `community`

Feasible as a first-class route because `louvainCommunityId` is already present.

First version can support:

- same-community DOI discovery
- same-community material/process/performance field summaries
- relationship/network style answers
- cluster explanation using representative labels and examples

Main risk:

- Community IDs need semantic labeling or representative extraction so answers do not expose raw IDs as the main user-facing concept.

Further inspection confirms that community IDs can support a first version of relationship/community answers.

For `LiFePO4` title matches, the largest communities included:

| Community ID | Matching DOI Count |
| --- | ---: |
| `585242` | 312 |
| `350622` | 247 |
| `514577` | 101 |
| `494438` | 64 |
| `251148` | 54 |
| `594082` | 46 |
| `44454` | 46 |

For community `585242`, the label profile included:

| Label | Count |
| --- | ---: |
| `discharge_capacity` | 16,591 |
| `name` | 10,248 |
| `morphology` | 4,476 |
| `cycling_stability` | 3,893 |
| `doi` | 1,713 |
| `coulombic_efficiency` | 1,433 |
| `conductivity` | 1,293 |
| `title` | 1,105 |
| `particle_size` | 1,092 |
| `surface_area` | 460 |

This suggests a community answer can be rendered from:

1. representative titles in the community,
2. frequent materials or sample names,
3. frequent preparation methods,
4. frequent raw materials,
5. dominant performance/property labels.

The community route should avoid presenting raw community IDs as user-facing meaning. Instead, it should synthesize labels such as "LiFePO4 preparation/performance cluster" from representative titles, materials, and methods.

## Data Quality Observations

The graph contains useful structured data, but route logic needs guardrails.

DOI/title observations:

- Total `:doi` nodes: 14,075.
- DOI nodes with one title: 12,091.
- DOI nodes without title: 1,948.
- DOI nodes with multiple titles: 35 total, including a small number of severe aggregation cases.
- Examples of aggregated/truncated DOI nodes include `10.1007/s12598-`, `10.1002/ani`, `10.1016/S1003`, and `10.1016/S1872`.
- A strict DOI regex matched 14,016 of 14,075 DOI nodes in the inspected query; a looser regex matched 14,040. Some values contain obvious text corruption.

Implications:

- Direct DOI lookup should validate DOI syntax before trusting it.
- Graph candidate DOI lists should filter suspicious DOI values before seeding RAG.
- A DOI connected to many unrelated titles should be treated as low-confidence or excluded from direct answers.
- The graph can still provide useful evidence from imperfect DOI nodes, but direct answers need stricter thresholds than graph-for-RAG hints.

Raw material observations:

- Raw material values contain many near-duplicates and casing variants, for example `LiFePO₄_null_null`, `LiFePO4_null_null`, `LiFePO₄ (LFP)_null_null`, and `LiFePO₄ (Lithium Iron Phosphate)_null_null`.
- Many values include suffixes such as `_null_null`, `_Not specified_Not specified`, or composition metadata.

Implication: entity canonicalization is required for high-quality listing and community summaries.

Recipe and process observations:

- `carbon_source` values are often clean enough for direct listing, for example `sucrose`, `Super-P`, `carbon black`, and `expandable graphite`.
- `carbon_source` also has many casing and spelling variants. A corrected `sucrose` query returned separate groups such as `sucrose`, `Sucrose`, `Sucrose (C12H22O11)`, `C12H22O11 (sucrose)`, and `citric acid and sucrose`.
- `carbon_content` values are mixed numeric/text values such as `40 wt%`, `20 wt.%`, `10%`, and `not specified`.
- `additives` and `doping_elements` often contain serialized dictionary-like strings. These are information-rich but need parsing before clean rendering.
- `Li_Fe_ratio`, `Fe_P_ratio`, and `other_ratios` contain both simple ratios and long multi-ratio recipe text.
- `preparation_method` values are natural-language method descriptions.
- `key_process_parameters` and `process_steps` are usually placeholder bucket nodes; their useful values are under child relationships.
- Direct `temperature`, `time`, `pressure`, and `atmosphere` children were not observed directly under `key_process_parameters` in the inspected sample. Useful parameter values appear mainly inside `calcination`, `milling`, `sintering`, and `drying` strings such as `temperature_700°C_time_6 h_atmosphere_Ar flow_heating_rate_10°C/min`.

Implication: process/recipe precise answers are feasible, but the first implementation should use field-specific extractors rather than treating all values as plain text.

Verified query behavior notes:

- Filtering should be placed before later `OPTIONAL MATCH` clauses or after a `WITH`; otherwise the filter may attach only to the optional pattern and fail to constrain the main rows.
- Direct `(:name)-[:discharge_capacity]->(:discharge_capacity)` often returns placeholder names. For LFP title/material queries, the useful capacity values were found through the two-hop pattern `(:name)-[:discharge_capacity]->(:discharge_capacity)-[:discharge_capacity]->(:discharge_capacity)`.
- Example two-hop capacity values included `0.5C_initial_141.2 mA h g⁻¹` and `0.5C_200cycles_130.4 mA h g⁻¹ (92.4% of initial)`.

## Vector Store Observations

The graph is not the vector store. Current fastQA vector paths in config point to Chroma directories under `resource/fastqa`.

Observed local Chroma stores:

| Store | Path | Collection | Dimension | Embeddings |
| --- | --- | --- | ---: | ---: |
| Summary/vector store | `resource/fastqa/vector_database` | `lfp_papers` | 1024 | 34,726 |
| MD/full-text store | `resource/fastqa/vector_database_md` | `md_papers` | 1024 | 686,266 |

Configured but not observed locally in this worktree:

- `resource/fastqa/vector_database_pdf`
- `resource/fastqa/community_vector_database`

Summary/vector metadata keys:

- `doi`
- `title`
- `source_file`
- `chunk_id`
- `data_quality`
- `chroma:document`

MD store metadata keys:

- `document_name`
- `filename`
- `chunk_id`
- `is_full_document`
- `chroma:document`

Implications:

- Graph-to-vector handoff can use DOI directly for the summary/vector store.
- MD expansion needs DOI-to-document-name normalization, because the MD store uses underscore-style `document_name`/`filename` rather than a direct `doi` metadata key.
- The four-route `semantic` and `hybrid` paths should continue to use Chroma for semantic evidence and use Neo4j only to seed DOI/entity/fact constraints.

## Current fastQA Registry Coverage

Current `fastQA/app/modules/graph_kb/schema_registry.py` covers only a small subset of the actual graph:

- `paper.doi`
- `paper.title`
- `raw_material.name`
- `process.method`
- `equipment.name`
- `testing.name`
- `recipe.name`
- `description.name`

Current allowed labels:

- `doi`
- `name`
- `title`
- `raw_materials`
- `process`
- `preparation_method`
- `recipe`
- `equipment`
- `testing`
- `description`

Current allowed relations:

- `title`
- `raw_materials`
- `process`
- `preparation_method`
- `recipe`
- `equipment`
- `testing`
- `description`
- `name`

Notable missing areas for the intended four-route behavior:

- performance fields: `compaction_density`, `tap_density`, `discharge_capacity`, `cycling_stability`, `conductivity`, `particle_size`, and related labels
- process parameter fields: `temperature`, `time`, `pressure`, `atmosphere`, `calcination`, `milling`, `sintering`, `drying`
- recipe fields: `carbon_source`, `carbon_content`, `additives`, `dopant`, `doping_elements`, `Li_Fe_ratio`, `Fe_P_ratio`, `other_ratios`
- community field: `louvainCommunityId`

Implication: the existing graph V2 code is a good scaffold, but the registry is not yet broad enough to recreate the four-route experience described in the legacy flow document.

## Open Questions For Further Exploration

- Which DOI validation threshold should separate direct graph answers from graph-for-RAG hints?
- Which field parsers should be promoted first for precise numeric filtering and ranking?
- How should serialized dictionary-like `additives` and `doping_elements` values be parsed and displayed?
- Should Neo4j full-text indexes be added for high-frequency substring paths, or should substring search remain a fallback behind Chroma/entity extraction?
- Should `community_vector_database` be regenerated or deprecated if community answers can be rendered from Neo4j communities plus normal Chroma evidence?
