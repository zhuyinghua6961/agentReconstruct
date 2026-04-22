from dataclasses import dataclass
import os
from pathlib import Path


_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}
_SERVICE_ROOT = Path(__file__).resolve().parent
_INITIAL_ENV_KEYS = frozenset(os.environ.keys())
_DEFAULT_ENV_FILES = (
    ("config.shared.env", False),
    ("config.secret.env", True),
    (".env", True),
)


def _strip_optional_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"\"", "'"}:
        return value[1:-1]
    return value


def _load_env_file(path: Path, *, override_loaded_values: bool = False) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        name, raw_value = line.split("=", 1)
        name = name.strip()
        if not name:
            continue
        if name in _INITIAL_ENV_KEYS:
            continue
        if not override_loaded_values and name in os.environ:
            continue
        os.environ[name] = _strip_optional_quotes(raw_value.strip())


def _load_default_env_files() -> None:
    for filename, override_loaded_values in _DEFAULT_ENV_FILES:
        _load_env_file(_SERVICE_ROOT / filename, override_loaded_values=override_loaded_values)


_load_default_env_files()


def _read_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    text = raw.strip().lower()
    if text in _TRUE_VALUES:
        return True
    if text in _FALSE_VALUES:
        return False
    raise ValueError(f"{name} must be a boolean value")



def _read_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    return int(raw)



def _read_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    return float(raw)


@dataclass(frozen=True)
class HttpSettings:
    host: str
    port: int


@dataclass(frozen=True)
class GunicornSettings:
    workers: int
    threads: int
    timeout: int
    keepalive: int
    max_requests: int
    max_requests_jitter: int
    worker_class: str


@dataclass(frozen=True)
class RuntimeSettings:
    ask_stream_max_concurrent: int
    ask_executor_max_workers: int


@dataclass(frozen=True)
class RedisSettings:
    url: str
    enabled: bool
    key_prefix: str
    socket_connect_timeout_sec: float
    socket_timeout_sec: float


@dataclass(frozen=True)
class AuthoritySettings:
    base_url: str
    timeout_seconds: float
    internal_token: str
    durable_enabled: bool


@dataclass(frozen=True)
class AuthSettings:
    jwt_secret: str
    jwt_expire_seconds: int
    jwt_compatible_access_salts: tuple[str, ...]


@dataclass(frozen=True)
class LlmHttpSettings:
    shared_pool_enabled: bool
    connect_timeout_seconds: float
    read_timeout_seconds: float
    stream_read_timeout_seconds: float
    write_timeout_seconds: float
    pool_timeout_seconds: float
    keepalive_expiry_seconds: float
    max_keepalive_connections: int
    max_connections: int


@dataclass(frozen=True)
class PlanningHotPoolSettings:
    enabled: bool
    lane_count: int
    warmup_enabled: bool
    warm_interval_seconds: float
    warm_timeout_seconds: float
    warm_jitter_seconds: float
    lane_degraded_after_seconds: float
    warm_active_start_hour: int
    warm_active_end_hour: int


@dataclass(frozen=True)
class PlanningUpstreamGateSettings:
    enabled: bool
    limit: int


@dataclass(frozen=True)
class PatentGraphSettings:
    enabled: bool
    v2_enabled: bool
    rag_injection_enabled: bool
    neo4j_url: str
    neo4j_username: str
    neo4j_password: str
    neo4j_database: str
    timeout_ms: int
    max_rows: int
    query_logging: bool


@dataclass(frozen=True)
class Settings:
    service_name: str
    durable_mode_enabled: bool
    patent_file_routes_enabled: bool
    runtime_env: str
    http: HttpSettings
    gunicorn: GunicornSettings
    runtime: RuntimeSettings
    redis: RedisSettings
    authority: AuthoritySettings
    auth: AuthSettings
    llm_http: LlmHttpSettings
    planning_hot_pool: PlanningHotPoolSettings
    planning_upstream_gate: PlanningUpstreamGateSettings
    graph_kb: PatentGraphSettings



def get_settings() -> Settings:
    compat_raw = str(os.getenv("JWT_COMPATIBLE_ACCESS_SALTS", "agentcode.auth.access") or "").strip()
    compat_salts = tuple(item.strip() for item in compat_raw.replace(";", ",").split(",") if item.strip())
    runtime_env = str(os.getenv("PATENT_ENV", "dev") or "dev").strip() or "dev"
    redis_key_prefix = str(os.getenv("PATENT_REDIS_KEY_PREFIX", "patent") or "patent").strip() or "patent"
    return Settings(
        service_name="patent",
        durable_mode_enabled=_read_bool("PATENT_DURABLE_MODE_ENABLED", True),
        patent_file_routes_enabled=_read_bool("PATENT_FILE_ROUTES_ENABLED", True),
        runtime_env=runtime_env,
        http=HttpSettings(
            host=os.getenv("PATENT_HOST", "0.0.0.0"),
            port=_read_int("PATENT_PORT", 8787),
        ),
        gunicorn=GunicornSettings(
            workers=_read_int("PATENT_GUNICORN_WORKERS", 4),
            threads=max(1, _read_int("PATENT_GUNICORN_THREADS", 8)),
            timeout=_read_int("PATENT_GUNICORN_TIMEOUT", 120),
            keepalive=max(1, _read_int("PATENT_GUNICORN_KEEPALIVE", 15)),
            max_requests=max(0, _read_int("PATENT_GUNICORN_MAX_REQUESTS", 1000)),
            max_requests_jitter=max(0, _read_int("PATENT_GUNICORN_MAX_REQUESTS_JITTER", 100)),
            worker_class=os.getenv("PATENT_GUNICORN_WORKER_CLASS", "uvicorn.workers.UvicornWorker"),
        ),
        runtime=RuntimeSettings(
            ask_stream_max_concurrent=max(1, _read_int("PATENT_ASK_STREAM_MAX_CONCURRENT", 8)),
            ask_executor_max_workers=max(1, _read_int("PATENT_ASK_EXECUTOR_MAX_WORKERS", 4)),
        ),
        redis=RedisSettings(
            url=os.getenv("PATENT_REDIS_URL", "redis://localhost:6379/0"),
            enabled=_read_bool("PATENT_REDIS_ENABLED", False),
            key_prefix=redis_key_prefix,
            socket_connect_timeout_sec=_read_float("PATENT_REDIS_SOCKET_CONNECT_TIMEOUT_SEC", 1.5),
            socket_timeout_sec=_read_float("PATENT_REDIS_SOCKET_TIMEOUT_SEC", 1.5),
        ),
        authority=AuthoritySettings(
            base_url=os.getenv("PATENT_AUTHORITY_BASE_URL", "http://public-service"),
            timeout_seconds=_read_float("PATENT_AUTHORITY_TIMEOUT_SECONDS", 10.0),
            internal_token=str(os.getenv("PATENT_AUTHORITY_INTERNAL_TOKEN", "") or "").strip(),
            durable_enabled=_read_bool("PATENT_DURABLE_AUTHORITY_ENABLED", False),
        ),
        auth=AuthSettings(
            jwt_secret=str(os.getenv("JWT_SECRET", "") or "").strip(),
            jwt_expire_seconds=_read_int("JWT_EXPIRE_SECONDS", 86400),
            jwt_compatible_access_salts=compat_salts,
        ),
        llm_http=LlmHttpSettings(
            shared_pool_enabled=_read_bool("PATENT_LLM_HTTP_SHARED_POOL_ENABLED", False),
            connect_timeout_seconds=_read_float("PATENT_LLM_HTTP_CONNECT_TIMEOUT_SECONDS", 15.0),
            read_timeout_seconds=_read_float("PATENT_LLM_HTTP_READ_TIMEOUT_SECONDS", 180.0),
            stream_read_timeout_seconds=_read_float("PATENT_LLM_HTTP_STREAM_READ_TIMEOUT_SECONDS", 600.0),
            write_timeout_seconds=_read_float("PATENT_LLM_HTTP_WRITE_TIMEOUT_SECONDS", 180.0),
            pool_timeout_seconds=_read_float("PATENT_LLM_HTTP_POOL_TIMEOUT_SECONDS", 30.0),
            keepalive_expiry_seconds=_read_float("PATENT_LLM_HTTP_KEEPALIVE_EXPIRY_SECONDS", 120.0),
            max_keepalive_connections=max(1, _read_int("PATENT_LLM_HTTP_MAX_KEEPALIVE_CONNECTIONS", 20)),
            max_connections=max(1, _read_int("PATENT_LLM_HTTP_MAX_CONNECTIONS", 100)),
        ),
        planning_hot_pool=PlanningHotPoolSettings(
            enabled=_read_bool("PATENT_PLANNING_HOT_POOL_ENABLED", False),
            lane_count=max(1, _read_int("PATENT_PLANNING_HOT_POOL_LANE_COUNT", 2)),
            warmup_enabled=_read_bool("PATENT_PLANNING_HOT_POOL_WARMUP_ENABLED", False),
            warm_interval_seconds=max(1.0, _read_float("PATENT_PLANNING_HOT_POOL_WARM_INTERVAL_SECONDS", 7200.0)),
            warm_timeout_seconds=max(1.0, _read_float("PATENT_PLANNING_HOT_POOL_WARM_TIMEOUT_SECONDS", 30.0)),
            warm_jitter_seconds=max(0.0, _read_float("PATENT_PLANNING_HOT_POOL_WARM_JITTER_SECONDS", 0.0)),
            lane_degraded_after_seconds=max(
                1.0,
                _read_float("PATENT_PLANNING_HOT_POOL_LANE_DEGRADED_AFTER_SECONDS", 7200.0),
            ),
            warm_active_start_hour=max(0, min(23, _read_int("PATENT_PLANNING_HOT_POOL_WARM_ACTIVE_START_HOUR", 8))),
            warm_active_end_hour=max(1, min(24, _read_int("PATENT_PLANNING_HOT_POOL_WARM_ACTIVE_END_HOUR", 18))),
        ),
        planning_upstream_gate=PlanningUpstreamGateSettings(
            enabled=_read_bool("PATENT_PLANNING_UPSTREAM_GATE_ENABLED", False),
            limit=max(1, _read_int("PATENT_PLANNING_UPSTREAM_GATE_LIMIT", 1)),
        ),
        graph_kb=PatentGraphSettings(
            enabled=_read_bool("PATENT_GRAPH_KB_ENABLED", False),
            v2_enabled=_read_bool("PATENT_GRAPH_KB_V2_ENABLED", False),
            rag_injection_enabled=_read_bool("PATENT_GRAPH_KB_RAG_INJECTION_ENABLED", False),
            neo4j_url=str(os.getenv("PATENT_NEO4J_URL", "bolt://127.0.0.1:8687") or "").strip(),
            neo4j_username=str(os.getenv("PATENT_NEO4J_USERNAME", "neo4j") or "neo4j").strip(),
            neo4j_password=str(os.getenv("PATENT_NEO4J_PASSWORD", "") or ""),
            neo4j_database=str(os.getenv("PATENT_NEO4J_DATABASE", "neo4j") or "neo4j").strip() or "neo4j",
            timeout_ms=max(100, _read_int("PATENT_GRAPH_KB_TIMEOUT_MS", 3000)),
            max_rows=max(1, _read_int("PATENT_GRAPH_KB_MAX_ROWS", 20)),
            query_logging=_read_bool("PATENT_GRAPH_KB_QUERY_LOGGING", False),
        ),
    )
