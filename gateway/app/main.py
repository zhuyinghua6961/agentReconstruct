"""Gateway application entrypoint."""

from __future__ import annotations

import logging

from fastapi import FastAPI

from app.core.config import GatewaySettings
from app.core.logging import setup_logging
from app.core.trace import trace_id_middleware
from app.integrations.redis.service import bootstrap_redis_runtime
from app.routers.admission import router as admission_router
from app.routers.health import router as health_router
from app.routers.public_proxy import router as public_proxy_router
from app.routers.qa import router as qa_router
from app.services.backend_registry import BackendRegistry
from app.services.conversation_persistence import ConversationPersistenceService
from app.services.execution_admission import build_admission_status
from app.services.execution_event_relay import ExecutionEventRelayStore
from app.services.execution_queue_status import ExecutionQueueStatusStore
from app.services.execution_slot_leases import ExecutionSlotLeaseStore
from app.services.conversation_files import ConversationFileService
from app.services.file_context_resolver import FileContextResolver
from app.services.provider_factory import build_conversation_file_provider
from app.services.proxy import ProxyService
from app.services.quota_proxy import QuotaProxyService
from app.services.route_classifier import ClassifierThresholdPolicy, NoopRouteClassifier
from app.services.route_decision import RouteDecisionService


logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = GatewaySettings.from_env()
    setup_logging(settings.debug)
    if settings.backend_config_warnings:
        for warning in settings.backend_config_warnings:
            logger.warning("gateway backend config warning: %s", warning)
        if settings.strict_backend_config:
            raise ValueError(f"invalid gateway backend configuration: {settings.backend_config_warnings}")

    app = FastAPI(title=settings.app_name, debug=settings.debug)
    app.middleware("http")(trace_id_middleware)

    redis_runtime = bootstrap_redis_runtime(settings.redis)

    app.state.settings = settings
    app.state.redis_runtime = redis_runtime
    app.state.execution_queue_status_store = ExecutionQueueStatusStore(redis_service=redis_runtime.service)
    app.state.execution_event_relay_store = ExecutionEventRelayStore(redis_service=redis_runtime.service)
    app.state.execution_slot_lease_store = ExecutionSlotLeaseStore(redis_service=redis_runtime.service)
    app.state.backend_registry = BackendRegistry(settings)
    app.state.conversation_file_service = ConversationFileService(
        provider=build_conversation_file_provider(settings),
    )
    app.state.file_context_resolver = FileContextResolver(
        route_classifier=NoopRouteClassifier(),
        classifier_enabled=settings.route_classifier.enabled,
        classifier_policy=ClassifierThresholdPolicy(
            high_confidence=settings.route_classifier.high_confidence_threshold,
            medium_confidence=settings.route_classifier.medium_confidence_threshold,
        ),
    )
    app.state.route_decision_service = RouteDecisionService()
    app.state.proxy_service = ProxyService(settings)
    app.state.quota_proxy_service = QuotaProxyService(settings)
    app.state.conversation_persistence_service = ConversationPersistenceService(settings)
    app.state.component_status = {
        "redis": redis_runtime.status.to_dict(),
        "admission": build_admission_status(
            settings=settings,
            redis_runtime=redis_runtime,
            queue_status_store=app.state.execution_queue_status_store,
            slot_lease_store=app.state.execution_slot_lease_store,
        ),
        "queue_status_store": app.state.execution_queue_status_store.describe(),
        "event_relay_store": app.state.execution_event_relay_store.describe(),
        "slot_lease_store": app.state.execution_slot_lease_store.describe(),
    }

    app.include_router(health_router)
    app.include_router(admission_router)
    app.include_router(public_proxy_router)
    app.include_router(qa_router)
    return app


app = create_app()
