from __future__ import annotations

from app.modules.graph_kb.canonicalizer import canonicalize_graph_rows
from app.modules.graph_kb.models import GraphKbQueryPlan, GraphQueryPlanV2


def test_canonicalizer_extracts_fact_rows_into_graph_evidence_bundle():
    bundle = canonicalize_graph_rows(
        plan=GraphQueryPlanV2(
            strategy="template",
            legacy_template_id="lookup_by_doi",
            legacy_template_plan=GraphKbQueryPlan(template_id="lookup_by_doi", params={"doi": "10.1000/test"}),
        ),
        rows=[{"doi": "10.1000/test", "title": "Test Paper", "raw_materials": ["LFP powder"]}],
    )

    assert bundle.doi_candidates == ("10.1000/test",)
    assert bundle.facts
    assert bundle.direct_answerable is True
