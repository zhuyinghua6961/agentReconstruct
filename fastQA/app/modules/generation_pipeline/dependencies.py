from __future__ import annotations

import logging

try:
    from dotenv import load_dotenv
except Exception:
    def load_dotenv(path=None):  # type: ignore
        logging.getLogger(__name__).warning("python-dotenv unavailable; skip loading env file")
        return None

from app.modules.microscopic_expert import MicroscopicSemanticExpert


SentenceAligner = None
