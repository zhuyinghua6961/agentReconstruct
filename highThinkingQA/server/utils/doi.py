"""Pure DOI normalization helpers for thinking ask flows."""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import unquote


def normalize_doi(value: str) -> str:
    text = str(value or "").strip()
    filename_like_source = False
    previous = None
    while previous != text:
        previous = text
        text = unquote(text).strip()
    text = text.replace("\\", "/")
    text = re.sub(r"^doi:\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^[(/\\s]+|[)\],;:.\\s]+$", "", text)
    if "papers/" in text:
        text = text.split("papers/", 1)[-1]
        filename_like_source = text.lower().endswith(".pdf")
    elif (
        text.lower().endswith(".pdf")
        and (
            os.path.isabs(text)
            or text.startswith("./")
            or text.startswith("../")
            or bool(re.match(r"^[A-Za-z]:[\\/]", text))
        )
    ):
        text = Path(text).name or text
        filename_like_source = True
    if text.lower().endswith(".pdf"):
        text = text[:-4]
    if "_" in text and "/" not in text and text.startswith("10.") and not filename_like_source:
        text = text.replace("_", "/", 1)
    return text.strip()
