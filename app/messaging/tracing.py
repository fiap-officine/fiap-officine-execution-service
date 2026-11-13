"""Integração com o New Relic para tracing distribuído através do SQS.

No publish, os headers W3C/New Relic são inseridos no envelope (campo `trace`).
No consumo, a mensagem é processada dentro de uma BackgroundTask que aceita
esses headers, de modo que o trace OS -> Billing -> OS -> Execution apareça
como uma única transação distribuída.

Todas as funções são no-op quando o agente não está instalado/ativo.
"""

import contextlib
import logging
from collections.abc import Iterator
from typing import Any

logger = logging.getLogger(__name__)


def _agent():
    try:
        import newrelic.agent

        return newrelic.agent
    except Exception:  # pragma: no cover - agente ausente
        return None


def inject_trace_headers() -> dict[str, str]:
    agent = _agent()
    if agent is None:
        return {}
    headers: list[tuple[str, str]] = []
    try:
        agent.insert_distributed_trace_headers(headers)
    except Exception:  # pragma: no cover
        return {}
    return {k: v for k, v in headers}


def accept_trace_headers(headers: dict[str, str] | None) -> None:
    agent = _agent()
    if agent is None or not headers:
        return
    try:
        agent.accept_distributed_trace_headers(
            list(headers.items()), transport_type="Queue"
        )
    except Exception:  # pragma: no cover
        logger.debug("Falha ao aceitar headers de tracing", exc_info=True)


@contextlib.contextmanager
def background_transaction(name: str, group: str = "Message") -> Iterator[None]:
    """Abre uma transação de background no New Relic (se houver agente ativo)."""
    agent = _agent()
    application = None
    if agent is not None:
        try:
            application = agent.application()
        except Exception:  # pragma: no cover
            application = None
    if application is None or not getattr(application, "enabled", False):
        yield
        return
    with agent.BackgroundTask(application, name=name, group=group):  # pragma: no cover
        yield


def record_event(event_type: str, attributes: dict[str, Any]) -> None:
    agent = _agent()
    if agent is None:
        return
    try:
        agent.record_custom_event(event_type, attributes)
    except Exception:  # pragma: no cover
        pass


def add_attributes(attributes: dict[str, Any]) -> None:
    agent = _agent()
    if agent is None:
        return
    for key, value in attributes.items():
        if value is None:
            continue
        try:
            agent.add_custom_attribute(key, value)
        except Exception:  # pragma: no cover
            pass
