import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

# ContextVars propagados ao longo do ciclo de vida da requisição / mensagem
correlation_id_ctx: ContextVar[str | None] = ContextVar(
    "correlation_id_ctx", default=None
)
saga_id_ctx: ContextVar[str | None] = ContextVar("saga_id_ctx", default=None)
os_id_ctx: ContextVar[int | None] = ContextVar("os_id_ctx", default=None)

EXTRA_KEYS = (
    "path",
    "method",
    "status_code",
    "duration_ms",
    "request_id",
    "os_id",
    "cliente_id",
    "saga_id",
    "message_id",
    "message_type",
    "queue",
)


def get_correlation_id() -> str | None:
    """Retorna o correlation ID do contexto atual."""
    return correlation_id_ctx.get()


# Campos de contexto copiados para todo LogRecord. Assim o encaminhamento de
# logs do agente New Relic (application_logging.forwarding.context_data) os
# envia como atributos pesquisáveis: context.saga_id, context.os_id, ...
# Não passe esses nomes em `extra=`: eles já vêm do contexto.
_CONTEXT_FIELDS = (
    ("correlation_id", correlation_id_ctx),
    ("saga_id", saga_id_ctx),
    ("os_id", os_id_ctx),
)
_base_record_factory = logging.getLogRecordFactory()


def _context_record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
    record = _base_record_factory(*args, **kwargs)
    for name, ctx in _CONTEXT_FIELDS:
        value = ctx.get()
        if value is not None:
            setattr(record, name, value)
    return record


class JSONLogFormatter(logging.Formatter):
    """
    Formatador de logs estruturados em JSON para envio ao New Relic e stdout.
    Inclui correlation_id, saga_id, os_id e identificadores de tracing distribuído.
    """

    def __init__(
        self,
        service_name: str = "officine-execution-service",
        environment: str = "production",
    ):
        super().__init__()
        self.service_name = service_name
        self.environment = environment

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "service": self.service_name,
            "environment": self.environment,
        }

        corr_id = get_correlation_id()
        if corr_id:
            log_entry["correlation_id"] = corr_id
        saga_id = saga_id_ctx.get()
        if saga_id:
            log_entry["saga_id"] = saga_id
        os_id = os_id_ctx.get()
        if os_id is not None:
            log_entry["os_id"] = os_id

        for key in EXTRA_KEYS:
            if hasattr(record, key):
                log_entry[key] = getattr(record, key)

        try:
            import newrelic.agent

            metadata = newrelic.agent.get_linking_metadata()
            if metadata:
                log_entry["trace_id"] = metadata.get("trace.id")
                log_entry["span_id"] = metadata.get("span.id")
                log_entry["entity_guid"] = metadata.get("entity.guid")
        except Exception:  # pragma: no cover - agente ausente
            pass

        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, ensure_ascii=False, default=str)


def setup_json_logging(
    service_name: str = "officine-execution-service",
    environment: str = "production",
    log_level: str = "INFO",
):
    """Configura o logger raiz para emitir logs estritamente em JSON no stdout."""
    logging.setLogRecordFactory(_context_record_factory)
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JSONLogFormatter(service_name=service_name, environment=environment)
    )
    root_logger.addHandler(handler)

    logging.getLogger("uvicorn.access").handlers = []
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("boto3").setLevel(logging.WARNING)

    return root_logger
