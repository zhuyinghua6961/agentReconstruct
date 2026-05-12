from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from typing import Any


STAGE2_GUARDRAIL_VERSION = "patent-stage2-guardrail-v1"
STAGE2_VALIDATION_VERSION = "patent-stage2-validation-v1"
STAGE2_SCORING_VERSION = "patent-stage2-scoring-v1"
STAGE2_PAYLOAD_CONTRACT_VERSION = "patent-stage2-payload-v2"
STAGE2_RERANK_ADAPTER_VERSION = "patent-stage2-rerank-adapter-v1"


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "")).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, default)).strip())
    except Exception:
        value = int(default)
    return max(minimum, min(maximum, value))


def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    try:
        value = float(str(os.getenv(name, default)).strip())
    except Exception:
        value = float(default)
    return max(float(minimum), min(float(maximum), value))


def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        raw = str(os.getenv(name, "") or "").strip()
        if raw:
            return raw
    return default


@dataclass(frozen=True)
class PatentStage2RuntimeToggles:
    convergence_enabled: bool
    force_keyword_injection_enabled: bool
    entity_lock_enabled: bool
    rerank_enabled: bool
    rerank_candidates: int
    rerank_top_patents: int
    min_results_per_claim: int
    max_results_per_claim: int
    max_global_patents: int
    validation_enabled: bool
    c_patent_scoring_enabled: bool
    c_global_chunk_recall_enabled: bool
    c_table_metric_boost_enabled: bool
    rerank_provider: str
    rerank_model: str
    rerank_base_url: str
    rerank_timeout_seconds: float
    rerank_endpoint_family: str


def resolve_stage2_runtime_toggles() -> PatentStage2RuntimeToggles:
    return PatentStage2RuntimeToggles(
        convergence_enabled=_env_bool("PATENT_STAGE2_CONVERGENCE_ENABLED", False),
        force_keyword_injection_enabled=_env_bool("PATENT_STAGE2_FORCE_KEYWORD_INJECTION", True),
        entity_lock_enabled=_env_bool("PATENT_STAGE2_ENTITY_LOCK_ENABLED", True),
        rerank_enabled=True,
        rerank_candidates=_env_int("PATENT_STAGE2_RERANK_CANDIDATES", 80, minimum=5, maximum=200),
        rerank_top_patents=_env_int("PATENT_STAGE2_RERANK_TOP_PATENTS", 20, minimum=1, maximum=100),
        min_results_per_claim=_env_int("PATENT_STAGE2_MIN_RESULTS_PER_CLAIM", 2, minimum=0, maximum=20),
        max_results_per_claim=_env_int("PATENT_STAGE2_MAX_RESULTS_PER_CLAIM", 5, minimum=1, maximum=50),
        max_global_patents=_env_int("PATENT_STAGE2_MAX_GLOBAL_PATENTS", 20, minimum=1, maximum=200),
        validation_enabled=_env_bool("PATENT_STAGE2_VALIDATION_ENABLED", True),
        c_patent_scoring_enabled=_env_bool("PATENT_STAGE2_C_PATENT_SCORING_ENABLED", False),
        c_global_chunk_recall_enabled=_env_bool("PATENT_STAGE2_C_GLOBAL_CHUNK_RECALL_ENABLED", False),
        c_table_metric_boost_enabled=_env_bool("PATENT_STAGE2_C_TABLE_METRIC_BOOST_ENABLED", False),
        rerank_provider=_first_env("RERANK_PROVIDER", default="none").lower() or "none",
        rerank_model=_first_env("RERANK_MODEL"),
        rerank_base_url=_first_env("RERANK_BASE_URL"),
        rerank_timeout_seconds=_env_float("RERANK_TIMEOUT_SECONDS", 20.0, minimum=0.5, maximum=300.0),
        rerank_endpoint_family=_first_env("RERANK_PROVIDER", default="none").lower() or "none",
    )


def build_stage2_runtime_signature(*, base_signature: dict[str, Any] | None = None) -> dict[str, Any]:
    toggles = resolve_stage2_runtime_toggles()
    signature = dict(base_signature or {})
    signature.update(
        {
            **{f"stage2_{key}": value for key, value in asdict(toggles).items()},
            "stage2_guardrail_version": STAGE2_GUARDRAIL_VERSION,
            "stage2_validation_version": STAGE2_VALIDATION_VERSION,
            "stage2_scoring_version": STAGE2_SCORING_VERSION,
            "stage2_payload_contract_version": STAGE2_PAYLOAD_CONTRACT_VERSION,
            "stage2_rerank_adapter_version": STAGE2_RERANK_ADAPTER_VERSION,
        }
    )
    return signature
