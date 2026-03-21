import os

from itsdangerous import URLSafeTimedSerializer

from server.services.auth_service import TokenService


def test_token_service_accepts_agentcode_compat_salt(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "compat-secret")
    monkeypatch.delenv("JWT_COMPATIBLE_ACCESS_SALTS", raising=False)

    token_service = TokenService()
    serializer = URLSafeTimedSerializer("compat-secret")
    token = serializer.dumps(
        {"user_id": 9, "role": "user", "iat": 1234567890},
        salt="agentcode.auth.access",
    )

    payload = token_service.decode_access_token(token)

    assert payload is not None
    assert int(payload["user_id"]) == 9
    assert payload["role"] == "user"


def test_token_service_rejects_unknown_salt(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "compat-secret")
    monkeypatch.setenv("JWT_COMPATIBLE_ACCESS_SALTS", "agentcode.auth.access")

    token_service = TokenService()
    serializer = URLSafeTimedSerializer("compat-secret")
    token = serializer.dumps(
        {"user_id": 9, "role": "user", "iat": 1234567890},
        salt="other.auth.access",
    )

    payload = token_service.decode_access_token(token)

    assert payload is None
