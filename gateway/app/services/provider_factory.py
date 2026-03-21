"""Factory for pluggable gateway providers."""

from __future__ import annotations

from app.core.config import GatewaySettings
from app.providers.conversation_files.noop import NoopConversationFileProvider
from app.providers.conversation_files.public_http import PublicHttpConversationFileProvider


def build_conversation_file_provider(settings: GatewaySettings):
    mode = str(settings.conversation_file_provider or "noop").strip().lower()
    if mode == "noop":
        return NoopConversationFileProvider()
    if mode == "public_http":
        return PublicHttpConversationFileProvider(
            base_url=settings.endpoints.public,
            timeout_seconds=min(settings.request_timeout_seconds, 10),
        )
    raise ValueError(f"unsupported conversation file provider: {mode}")
