"""脱敏记录上游模型鉴权状态。"""

from __future__ import annotations

import hashlib
import logging
from threading import Lock
from typing import Any

_SUCCESS_KEYS: set[tuple[str, str, str, str, str]] = set()
_LOCK = Lock()


def _normalize_api_key(api_key: str | None) -> str:
    value = str(api_key or "").strip()
    scheme, separator, token = value.partition(" ")
    if separator and scheme.lower() == "bearer":
        return token.strip()
    return value


def _key_fingerprint(api_key: str | None) -> str:
    value = _normalize_api_key(api_key)
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _key_input_has_bearer(api_key: str | None) -> bool:
    return str(api_key or "").strip().lower().startswith("bearer ")


def _status_code_from_exception(exc: Exception | None) -> int | None:
    if exc is None:
        return None
    for candidate in (
        getattr(exc, "status_code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    ):
        try:
            if candidate is not None:
                return int(candidate)
        except Exception:
            continue
    return None


def _status_code_value(status_code: Any = None, exc: Exception | None = None) -> int | None:
    if status_code is not None:
        try:
            return int(status_code)
        except Exception:
            pass
    return _status_code_from_exception(exc)


def log_upstream_auth_success_once(
    *,
    logger: logging.Logger,
    service: str,
    endpoint: str,
    model: str,
    base_url: str,
    api_key: str | None,
    status_code: Any = None,
) -> None:
    fingerprint = _key_fingerprint(api_key)
    key = (str(service), str(endpoint), str(model), str(base_url), fingerprint)
    with _LOCK:
        if key in _SUCCESS_KEYS:
            return
        _SUCCESS_KEYS.add(key)
    logger.info(
        "LLM upstream auth ok service=%s endpoint=%s model=%s base_url=%s status_code=%s key_present=%s key_input_has_bearer=%s key_fingerprint=%s",
        service,
        endpoint,
        model,
        base_url,
        _status_code_value(status_code),
        bool(_normalize_api_key(api_key)),
        _key_input_has_bearer(api_key),
        fingerprint or "-",
    )


def log_upstream_auth_failure(
    *,
    logger: logging.Logger,
    service: str,
    endpoint: str,
    model: str,
    base_url: str,
    api_key: str | None,
    status_code: Any = None,
    exc: Exception | None = None,
) -> None:
    resolved_status = _status_code_value(status_code, exc)
    if resolved_status not in {401, 403}:
        return
    logger.warning(
        "LLM upstream auth failed service=%s endpoint=%s model=%s base_url=%s status_code=%s key_present=%s key_input_has_bearer=%s key_fingerprint=%s",
        service,
        endpoint,
        model,
        base_url,
        resolved_status,
        bool(_normalize_api_key(api_key)),
        _key_input_has_bearer(api_key),
        _key_fingerprint(api_key) or "-",
    )


def reset_upstream_auth_log_state_for_tests() -> None:
    with _LOCK:
        _SUCCESS_KEYS.clear()
