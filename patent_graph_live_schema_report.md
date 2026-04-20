# Patent graph live schema vs code expectations

## Scope

This is a read-only exploration of the live patent graph database at `bolt://127.0.0.1:8687`, compared against the code under `patent/server/patent/graph_kb/`.

I inspected:

- live node labels
- live relationship types
- live node/relationship property metadata
- live relationship patterns between labels
- the exact labels, relationships, and properties used by `patent/server/patent/graph_kb/client.py`

## Connection note

The live local Neo4j instance did not accept the patent config defaults of `neo4j` plus empty password. It required a non-default password. This is a runtime/config mismatch, not a schema mismatch, but it matters operationally because `patent/config.py` defaults alone are not sufficient to connect to the active local DB.

## Executive summary

The result is structurally good for the current patent graph_kb code:

- every node label the code expects exists in the live DB
- every relationship type the code expects exists in the live DB
- every label-to-label relationship pattern the code traverses exists in the live DB
- every property the code reads from those expected node labels exists in the live DB schema

The live DB is richer than the current code path:

- extra labels exist: `Atmosphere`, `EmbodimentInsight`
- extra relationship types exist: `CO_OCCURS_WITH`, `NEXT_STEP`, `USES_ATMOSPHERE`, `HAS_EMBODIMENT_INSIGHT`
- several relationships carry properties that the code never reads
- some node properties have broader type variability than the code implicitly assumes

The biggest practical gap is not missing schema. It is that the live DB contains many more patents than the current graph_kb path will actually answer on, because the code intentionally filters `stub` patents.

## 1. What the code expects

From `patent/server/patent/graph_kb/client.py`, the graph_kb code expects these node labels:

- `Patent`
- `IPC`
- `IPCPrefix`
- `Organization`
- `Person`
- `ProcessStep`
- `StepTemplate`
- `MaterialRole`
- `Material`
- `ExperimentTable`
- `TableRow`
- `Measurement`
- `TechnicalProblem`
- `TechnicalSolution`
- `ApplicationScenario`
- `InventivePoint`
- `PerformanceFact`
- `ProtectionScope`
- `ClaimStepLabel`

It expects these relationship types:

- `CLASSIFIED_AS`
- `IN_IPC_SUBCLASS`
- `HAS_APPLICANT`
- `HAS_AGENCY`
- `HAS_INVENTOR`
- `HAS_PROCESS_STEP`
- `INSTANCE_OF`
- `HAS_MATERIAL_ROLE`
- `OPTION_INCLUDES`
- `HAS_EXPERIMENT_TABLE`
- `HAS_ROW`
- `HAS_MEASUREMENT`
- `ADDRESSES`
- `PROPOSES`
- `HAS_APPLICATION_SCENARIO`
- `HAS_INVENTIVE_POINT`
- `HAS_PERFORMANCE_FACT`
- `PROTECTION_INCLUDES`
- `CLAIM_INCLUDES_STEP`
- `CITES_PATENT`

It expects these main node properties:

- `Patent`: `patent_id`, `title`, `abstract`, `application_date`, `publication_date`, `ipc_main`, `patent_type`, `legal_status`, `source_file`, `stub`
- `IPC`: `code`
- `IPCPrefix`: `subclass`
- `Organization`: `name`
- `Person`: `name`
- `ProcessStep`: `order`, `name`, `operation`, `params_json`
- `StepTemplate`: `label`
- `MaterialRole`: `type`, `role`, `ratio`, `note`
- `Material`: `name`, `material_type`, `canonical_key`
- `ExperimentTable`: `table_title`, `table_index`
- `TableRow`: `sample_label`, `row_index`, `process_note`
- `Measurement`: `metric_key`, `value_raw`, `unit_hint`
- `TechnicalProblem`: `text`
- `TechnicalSolution`: `text`
- `ApplicationScenario`: `text`
- `InventivePoint`: `text`, `category`
- `PerformanceFact`: `text`, `category`
- `ProtectionScope`: `text`, `kind`
- `ClaimStepLabel`: `name`

## 2. Live node labels

Live labels and counts:

| Label | Count |
| --- | ---: |
| `Measurement` | 698,092 |
| `TableRow` | 128,310 |
| `InventivePoint` | 128,003 |
| `PerformanceFact` | 92,418 |
| `ProcessStep` | 64,091 |
| `MaterialRole` | 60,670 |
| `StepTemplate` | 59,538 |
| `ClaimStepLabel` | 46,327 |
| `Patent` | 39,517 |
| `ProtectionScope` | 38,107 |
| `Material` | 37,705 |
| `Person` | 23,661 |
| `ExperimentTable` | 15,365 |
| `Atmosphere` | 13,530 |
| `TechnicalSolution` | 12,489 |
| `TechnicalProblem` | 12,488 |
| `ApplicationScenario` | 12,487 |
| `Organization` | 5,206 |
| `IPC` | 2,956 |
| `EmbodimentInsight` | 1,550 |
| `IPCPrefix` | 189 |

Comparison against code:

- expected labels missing from DB: none
- extra labels present in DB but unused by current graph_kb code:
  - `Atmosphere`
  - `EmbodimentInsight`

## 3. Live relationship types

Live relationship types and counts:

| Relationship type | Count |
| --- | ---: |
| `CO_OCCURS_WITH` | 1,014,076 |
| `HAS_MEASUREMENT` | 698,092 |
| `OPTION_INCLUDES` | 205,366 |
| `HAS_ROW` | 128,310 |
| `HAS_INVENTIVE_POINT` | 128,003 |
| `HAS_PERFORMANCE_FACT` | 92,418 |
| `HAS_PROCESS_STEP` | 64,091 |
| `INSTANCE_OF` | 64,091 |
| `HAS_MATERIAL_ROLE` | 60,670 |
| `HAS_INVENTOR` | 60,055 |
| `CLASSIFIED_AS` | 59,171 |
| `NEXT_STEP` | 51,809 |
| `CITES_PATENT` | 48,377 |
| `CLAIM_INCLUDES_STEP` | 46,327 |
| `PROTECTION_INCLUDES` | 38,107 |
| `IN_IPC_SUBCLASS` | 20,268 |
| `HAS_APPLICANT` | 15,842 |
| `HAS_EXPERIMENT_TABLE` | 15,365 |
| `USES_ATMOSPHERE` | 13,530 |
| `PROPOSES` | 12,489 |
| `ADDRESSES` | 12,488 |
| `HAS_APPLICATION_SCENARIO` | 12,487 |
| `HAS_AGENCY` | 12,345 |
| `HAS_EMBODIMENT_INSIGHT` | 1,550 |

Comparison against code:

- expected relationship types missing from DB: none
- extra relationship types present in DB but unused by current graph_kb code:
  - `CO_OCCURS_WITH`
  - `NEXT_STEP`
  - `USES_ATMOSPHERE`
  - `HAS_EMBODIMENT_INSIGHT`

## 4. Live relationship patterns

Every relationship pattern the code uses exists in the live DB:

- `Patent -[:CLASSIFIED_AS]-> IPC`
- `Patent -[:IN_IPC_SUBCLASS]-> IPCPrefix`
- `Patent -[:HAS_APPLICANT]-> Organization`
- `Patent -[:HAS_AGENCY]-> Organization`
- `Patent -[:HAS_INVENTOR]-> Person`
- `Patent -[:HAS_PROCESS_STEP]-> ProcessStep`
- `ProcessStep -[:INSTANCE_OF]-> StepTemplate`
- `Patent -[:HAS_MATERIAL_ROLE]-> MaterialRole`
- `MaterialRole -[:OPTION_INCLUDES]-> Material`
- `Patent -[:HAS_EXPERIMENT_TABLE]-> ExperimentTable`
- `ExperimentTable -[:HAS_ROW]-> TableRow`
- `TableRow -[:HAS_MEASUREMENT]-> Measurement`
- `Patent -[:ADDRESSES]-> TechnicalProblem`
- `Patent -[:PROPOSES]-> TechnicalSolution`
- `Patent -[:HAS_APPLICATION_SCENARIO]-> ApplicationScenario`
- `Patent -[:HAS_INVENTIVE_POINT]-> InventivePoint`
- `Patent -[:HAS_PERFORMANCE_FACT]-> PerformanceFact`
- `Patent -[:PROTECTION_INCLUDES]-> ProtectionScope`
- `Patent -[:CLAIM_INCLUDES_STEP]-> ClaimStepLabel`
- `Patent -[:CITES_PATENT]-> Patent`

Extra relationship patterns not used by current graph_kb code:

- `ProcessStep -[:NEXT_STEP]-> ProcessStep`
- `Patent -[:USES_ATMOSPHERE]-> Atmosphere`
- `Patent -[:HAS_EMBODIMENT_INSIGHT]-> EmbodimentInsight`
- `Material -[:CO_OCCURS_WITH]-> StepTemplate`

## 5. Actual node properties for Patent and key labels

### Patent

Count: `39,517`

Code-used properties all exist:

- `patent_id`
- `title`
- `abstract`
- `application_date`
- `publication_date`
- `ipc_main`
- `patent_type`
- `legal_status`
- `source_file`
- `stub`

Additional live properties not used by current graph_kb code:

- `gid`
- `_labels`

Schema metadata:

- `patent_id` is mandatory `String`
- `gid` is mandatory `String`
- `_labels` is mandatory `StringArray`
- the rest are optional `String`
- `stub` is optional `Boolean`

Coverage details:

- total patents: `39,517`
- patents with `title`: `13,529`
- patents with `abstract`: `13,529`
- patents with `application_date`: `13,530`
- patents with `publication_date`: `13,530`
- patents with `ipc_main`: `13,530`
- patents with `patent_type`: `13,530`
- patents with `legal_status`: `13,529`
- patents with `source_file`: `13,530`
- patents with `stub`: `28,825`

Stub breakdown:

- `stub = true`: `28,825`
- `stub = false`: `0`
- `stub IS NULL`: `10,692`

Implication for code:

- the code treats `stub=true` as filtered/suppressed
- the code treats `stub=null` as non-stub and answerable
- a large portion of the live patent graph is intentionally excluded by current graph_kb behavior

### IPC

Count: `2,956`

Code-used property:

- `code`

Additional live properties:

- `gid`
- `_labels`

### IPCPrefix

Count: `189`

Code-used property:

- `subclass`

Additional live properties:

- `gid`
- `_labels`

### Organization

Count: `5,206`

Code-used property:

- `name`

Additional live properties:

- `kind`
- `gid`
- `_labels`

### Person

Count: `23,661`

Code-used property:

- `name`

Additional live properties:

- `gid`
- `_labels`

### ProcessStep

Count: `64,091`

Code-used properties all exist:

- `order`
- `name`
- `operation`
- `params_json`

Additional live properties:

- `preferred`
- `gid`
- `_labels`

Important type detail:

- `order` is not globally uniform in schema metadata
- Neo4j reports `order` as one of `Long`, `String`, or `Double`

Implication for code:

- the code assumes `step.order` is orderable and then sorts again in Python
- if a single result set ever contains mixed `step_order` types, Python-side sorting is a potential risk

### StepTemplate

Count: `59,538`

Code-used property:

- `label`

Additional live properties:

- `template_key`
- `gid`
- `_labels`

### MaterialRole

Count: `60,670`

Code-used properties all exist:

- `type`
- `role`
- `ratio`
- `note`

Additional live properties:

- `preferred`
- `gid`
- `_labels`

### Material

Count: `37,705`

Code-used properties all exist:

- `name`
- `material_type`
- `canonical_key`

Additional live properties:

- `gid`
- `_labels`

### ExperimentTable

Count: `15,365`

Code-used properties all exist:

- `table_title`
- `table_index`

Additional live properties:

- `columns_json`
- `gid`
- `_labels`

### TableRow

Count: `128,310`

Code-used properties all exist:

- `sample_label`
- `row_index`
- `process_note`

Additional live properties:

- `gid`
- `_labels`

### Measurement

Count: `698,092`

Code-used properties all exist:

- `metric_key`
- `value_raw`
- `unit_hint`

Additional live properties:

- `gid`
- `_labels`

Schema detail:

- `value_raw` is mandatory
- `metric_key` is optional
- `unit_hint` is optional

That aligns with the renderer’s fallback behavior for missing metric names.

### TechnicalProblem

Count: `12,488`

Code-used property:

- `text`

Additional live properties:

- `gid`
- `_labels`

### TechnicalSolution

Count: `12,489`

Code-used property:

- `text`

Additional live properties:

- `gid`
- `_labels`

### ApplicationScenario

Count: `12,487`

Code-used property:

- `text`

Additional live properties:

- `gid`
- `_labels`

### InventivePoint

Count: `128,003`

Code-used properties:

- `text`
- `category`

Additional live properties:

- `gid`
- `_labels`

### PerformanceFact

Count: `92,418`

Code-used properties:

- `text`
- `category`

Additional live properties:

- `gid`
- `_labels`

### ProtectionScope

Count: `38,107`

Code-used properties:

- `text`
- `kind`

Additional live properties:

- `gid`
- `_labels`

### ClaimStepLabel

Count: `46,327`

Code-used property:

- `name`

Additional live properties:

- `gid`
- `_labels`

## 6. Extra live labels not used by current code

### Atmosphere

Count: `13,530`

Observed properties:

- `gid`
- `options`
- `preferred`
- `_labels`

Observed pattern:

- `Patent -[:USES_ATMOSPHERE]-> Atmosphere`

Meaning:

- the live DB has atmosphere/process-environment structure that current graph_kb answers ignore

### EmbodimentInsight

Count: `1,550`

Observed properties:

- `gid`
- `conclusion`
- `insight_type`
- `_labels`

Observed pattern:

- `Patent -[:HAS_EMBODIMENT_INSIGHT]-> EmbodimentInsight`

Meaning:

- the live DB contains embodiment-level insight summaries that current graph_kb answers ignore

## 7. Relationship properties present in the DB

Most expected graph_kb relationships are property-less in practice. The live DB does have these relationship properties:

- `CO_OCCURS_WITH.weight`
- `HAS_INVENTIVE_POINT.category`
- `HAS_MATERIAL_ROLE.role`
- `HAS_PERFORMANCE_FACT.category`
- `PROTECTION_INCLUDES.kind`

Comparison to code:

- current graph_kb code never reads relationship properties
- for `HAS_INVENTIVE_POINT`, `HAS_MATERIAL_ROLE`, `HAS_PERFORMANCE_FACT`, and `PROTECTION_INCLUDES`, the same semantics are also present on the target nodes, so current code still works
- `CO_OCCURS_WITH.weight` is completely unused by the current graph_kb code

## 8. Schema comparison: live DB vs current graph_kb code

### Matches

These are the strong matches:

- all expected labels exist
- all expected relationship types exist
- all expected relationship traversal patterns exist
- all code-used node properties exist on the relevant labels
- no expected label or relationship type is missing

### Richer-than-code parts of the DB

The live DB contains extra structure the current graph_kb package does not use:

- `Atmosphere`
- `EmbodimentInsight`
- `NEXT_STEP` process sequencing
- `USES_ATMOSPHERE`
- `HAS_EMBODIMENT_INSIGHT`
- `CO_OCCURS_WITH.weight`

### Practical mismatches or risk points

1. Active DB auth does not match patent config defaults.
   The code defaults to empty password, but the active local DB requires a non-default password.

2. `Patent` answerability is much narrower than `Patent` node count suggests.
   There are `39,517` patent nodes, but only `10,692` have `stub IS NULL`, and the current graph_kb path suppresses `stub=true` patents.

3. `Patent` metadata fields are optional in the live schema.
   `title`, `abstract`, `legal_status`, and date fields are not mandatory. This matches the code’s fallback behavior, but it means direct lookup can still return render-empty for structurally valid nodes.

4. `ProcessStep.order` is type-heterogeneous at schema level.
   The code effectively assumes a cleaner ordering domain than the live DB guarantees.

5. The DB contains process and insight structures the code ignores.
   `NEXT_STEP`, `USES_ATMOSPHERE`, `Atmosphere`, `HAS_EMBODIMENT_INSIGHT`, and `EmbodimentInsight` are all present but unused by current query templates.

## 9. Bottom line

For the current `patent/server/patent/graph_kb/` implementation, the live patent graph schema is compatible.

There is no structural blocker such as missing labels, missing relationship types, or missing code-used properties.

The main findings are:

- compatibility is good
- the live graph is richer than the code path
- runtime auth defaults are not enough for the active local DB
- the code intentionally uses only a subset of the graph, especially because of `stub` filtering
