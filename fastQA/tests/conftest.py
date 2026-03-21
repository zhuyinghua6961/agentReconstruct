from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.main import app
from app.services.limits import AskConcurrencyLimiter


@pytest.fixture(autouse=True)
def _reset_app_state_between_tests():
    app.state.ask_limiter = AskConcurrencyLimiter(max_concurrent=app.state.settings.ask_stream_max_concurrent)
    app.state.pdf_web_bindings = None
    app.state.aux_llm = None
    yield
    app.state.ask_limiter = AskConcurrencyLimiter(max_concurrent=app.state.settings.ask_stream_max_concurrent)
    app.state.pdf_web_bindings = None
    app.state.aux_llm = None
