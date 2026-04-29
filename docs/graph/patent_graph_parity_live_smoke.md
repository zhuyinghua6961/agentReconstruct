# Patent Graph Parity Live Smoke

> Date: 2026-04-29
>
> Scope: manual smoke checklist for validating patent graph QA against the local patent Neo4j instance. Do not commit or print credentials.

## Preconditions

- Patent backend config has graph KB enabled.
- Local Neo4j is reachable from the approved runtime environment.
- Use the `agent` conda environment for direct local checks when needed.
- Secrets must come from local env/resource secret files and must not be copied into logs or this document.

Recommended command style:

```bash
conda run -n agent <command>
```

If socket/network access is blocked by sandboxing, request escalation once and run the smoke command directly with approval.

## Expected Log Events

For graph-attempted `kb_qa` requests, backend logs should show:

- `patent_graph.route_start`
- `patent_graph.slots_done`
- `patent_graph.classify_done`
- `patent_graph.plan_done`
- `patent_graph.execute_done`
- `patent_graph.canonicalize_done`
- `patent_graph.rag_payload_done`
- `patent_graph.direct_render_done` for direct candidates
- `patent_graph.route_end`

For graph-for-RAG requests that reach Stage2, final metadata should expose one of:

- `filter_applied`
- `bias_applied`
- `hint_only`
- `fallback_no_vector_hits`

## Smoke Matrix

| Question | Expected behavior |
| --- | --- |
| `CN100355122C 这件专利是什么？` | Direct graph if metadata usable; otherwise graph-for-RAG with explicit downgrade, not because `stub=true` alone. |
| `CN100355122C 的工艺步骤是什么？` | Direct graph via `list_patent_process_steps`; `stub=true` does not block populated step rows. |
| `CN100355122C 使用了哪些原料？` | Direct graph via `list_patent_material_roles`. |
| `CN100355122C 的技术问题和技术方案是什么？` | Direct graph via `list_patent_problem_solution`. |
| `CN100355122C 的发明点和保护范围是什么？` | Direct graph or graph-for-RAG if too long, with inventive/scope facts present. |
| `CN100355122C 的气氛条件是什么？` | `list_patent_atmospheres` outranks generic lookup. |
| `CN101209823B 的实施例洞察是什么？` | `list_patent_embodiment_insights` uses `HAS_EMBODIMENT_INSIGHT`. |
| `比较 CN100355122C 和 CN100369314C 的工艺步骤差异` | Graph-for-RAG with per-patent process evidence and Stage2 behavior metadata. |
| `宁德时代新能源科技股份有限公司有哪些专利？` | Direct applicant listing. |
| `发明人李长东有哪些专利？` | Direct inventor listing. |
| `H01M 下有哪些专利？` | IPCPrefix listing. |
| `H01M 有多少专利？` | IPCPrefix count. |
| `H01M10 下有哪些专利？` | `IPC.code STARTS WITH "H01M10"`, not `IPCPrefix.subclass = "H01M10"`. |
| `H01M10 有多少专利？` | IPC code-prefix count. |
| `H01M10/0525 有多少专利？` | Full `IPC.code` count. |
| `为什么喷雾干燥能提升磷酸铁锂性能？` | Graph-for-RAG when process/material/performance graph evidence exists; otherwise vector-only with clear skip reason. |
| `10.xxxx/xxxx 这篇文献是什么？` | Skip graph and preserve vector/RAG behavior. |

## Gateway Route Boundary Checks

| Gateway route shape | Expected patent graph behavior |
| --- | --- |
| KB question routed as `kb_qa` | Patent graph preflight may run. |
| Uploaded PDF question routed as `pdf_qa` | Patent graph preflight must not run. |
| Uploaded table question routed as `tabular_qa` | Patent graph preflight must not run. |
| Gateway-level file/KB mixed `hybrid_qa` | Patent graph preflight must not run unless a later spec changes file-route semantics. |

## Pass Criteria

- Direct graph, graph-for-RAG, and skip_graph are all observed.
- Specific facet templates outrank generic patent lookup.
- IPC grain is correct for `H01M`, `H01M10`, and `H01M10/0525`.
- Stage2 graph behavior is visible in metadata for graph-for-RAG.
- Existing vector/RAG path still answers broad non-graph questions.
- No secret values appear in logs, docs, fixtures, or command output.
