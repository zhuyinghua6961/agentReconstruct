from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


RetrievalBackend = Literal["exact_id", "metadata_lexical", "fulltext_lexical", "hybrid_no_vector", "vector_hybrid"]


@dataclass(frozen=True)
class PatentClaim:
    claim_number: int
    text: str


@dataclass(frozen=True)
class PatentDescriptionSnippet:
    paragraph_id: str
    text: str


@dataclass(frozen=True)
class PatentTableSupplement:
    table_title: str
    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, str]] = field(default_factory=list)
    source_image: str | None = None


@dataclass(frozen=True)
class PatentCatalogRecord:
    canonical_patent_id: str
    publication_number: str
    application_number: str | None
    title: str
    abstract_text: str
    applicant_names: list[str] = field(default_factory=list)
    inventor_names: list[str] = field(default_factory=list)
    ipc_codes: list[str] = field(default_factory=list)
    cpc_codes: list[str] = field(default_factory=list)
    claims: list[PatentClaim] = field(default_factory=list)
    description_snippets: list[PatentDescriptionSnippet] = field(default_factory=list)
    country: str = ""
    kind_code: str = ""
    publication_date: str = ""
    provider: str = "patent_source_x"
    original_available: bool = True


@dataclass(frozen=True)
class PatentEvidence:
    canonical_patent_id: str
    publication_number: str
    application_number: str | None
    title: str
    abstract_text: str
    claims: list[PatentClaim] = field(default_factory=list)
    description_snippets: list[PatentDescriptionSnippet] = field(default_factory=list)
    provider: str = "patent_source_x"
    original_available: bool = True
    country: str = ""
    kind_code: str = ""
    publication_date: str = ""
    matched_section_type: str = ""
    matched_section_label: str = ""
    matched_snippet: str = ""
    claim_number: int | None = None
    paragraph_id: str | None = None
    table_supplements: list[PatentTableSupplement] = field(default_factory=list)
    abstract_score: float | None = None
    chunk_score: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PatentRetrievalOutcome:
    retrieval_backend: RetrievalBackend
    retrieval_version: str
    catalog_index_version: str
    references: list[str]
    reference_objects: list[dict[str, object]]
    reference_links: list[dict[str, object]]
    original_links: list[dict[str, object]]
    evidences: list[PatentEvidence]
    answer_text: str = ""
    cache_hit: bool = False
    negative_cache_hit: bool = False
    not_found: bool = False
    timings: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PatentStage2RetrievalResult:
    documents: list[str] = field(default_factory=list)
    metadatas: list[dict[str, object]] = field(default_factory=list)
    distances: list[float | None] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    reference_objects: list[dict[str, object]] = field(default_factory=list)
    reference_links: list[dict[str, object]] = field(default_factory=list)
    original_links: list[dict[str, object]] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)
    cache_hit: bool = False
    negative_cache_hit: bool = False
    not_found: bool = False
    timings: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PatentMatchedEvidence:
    section_type: str
    section_label: str
    text: str
    anchor: dict[str, object] = field(default_factory=dict)
    scores: dict[str, float | None] = field(default_factory=dict)


@dataclass(frozen=True)
class PatentEvidenceBundle:
    canonical_patent_id: str
    title: str
    abstract_text: str
    matched_evidence: list[PatentMatchedEvidence] = field(default_factory=list)
    table_supplements: list[PatentTableSupplement] = field(default_factory=list)
    reference_object: dict[str, object] = field(default_factory=dict)
    reference_link: dict[str, object] | None = None
    original_links: list[dict[str, object]] = field(default_factory=list)
    scores: dict[str, float | None] = field(default_factory=dict)
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PatentSynthesisResult:
    success: bool
    final_answer: str
    references: list[str] = field(default_factory=list)
    reference_objects: list[dict[str, object]] = field(default_factory=list)
    reference_links: list[dict[str, object]] = field(default_factory=list)
    original_links: list[dict[str, object]] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)
    answer_text: str = ""


@dataclass(frozen=True)
class PatentStage3EvidenceResult:
    source_ids: list[str]
    evidences: list[PatentEvidenceBundle] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)
