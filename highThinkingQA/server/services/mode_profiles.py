"""Runtime profiles for fast/thinking/patent modes."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeProfile:
    mode: str
    enable_thinking: bool
    num_sub_questions: int
    retrieval_top_k: int
    max_check_loops: int
    implemented: bool = True


FAST_PROFILE = RuntimeProfile(
    mode="fast",
    enable_thinking=False,
    num_sub_questions=3,
    retrieval_top_k=2,
    max_check_loops=0,
    implemented=True,
)

THINKING_PROFILE = RuntimeProfile(
    mode="thinking",
    enable_thinking=True,
    num_sub_questions=5,
    retrieval_top_k=3,
    max_check_loops=2,
    implemented=True,
)

PATENT_PROFILE = RuntimeProfile(
    mode="patent",
    enable_thinking=True,
    num_sub_questions=5,
    retrieval_top_k=3,
    max_check_loops=2,
    implemented=False,
)


_MODE_PROFILES: dict[str, RuntimeProfile] = {
    "fast": FAST_PROFILE,
    "thinking": THINKING_PROFILE,
    "patent": PATENT_PROFILE,
}


def get_runtime_profile(mode: str) -> RuntimeProfile:
    mode_key = str(mode or "").strip().lower()
    if mode_key not in _MODE_PROFILES:
        raise KeyError(mode_key)
    return _MODE_PROFILES[mode_key]
