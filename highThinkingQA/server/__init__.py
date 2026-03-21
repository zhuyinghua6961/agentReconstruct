"""Shared backend package for the FastAPI service."""

from env_loader import load_workspace_env

load_workspace_env(override_existing=False)

__all__: list[str] = []
