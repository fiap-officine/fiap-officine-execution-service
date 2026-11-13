"""Consumer SQS do Execution Service.

A idempotência é garantida dentro de cada handler: o item `MSG#<message_id>`
entra no mesmo TransactWriteItems do efeito, com `attribute_not_exists(PK)`.
"""

import json
import logging
from collections.abc import Callable
from typing import Literal

from pydantic import ValidationError

from app.core.logging import correlation_id_ctx, os_id_ctx, saga_id_ctx
from app.messaging.envelope import Envelope
from app.messaging.sqs import SqsBus
from app.messaging.tracing import (
    accept_trace_headers,
    add_attributes,
    background_transaction,
    record_event,
)

logger = logging.getLogger(__name__)

Outcome = Literal["processed", "duplicate", "invalid"]
Handler = Callable[[Envelope], Outcome]


def handle_message(body: str, handler: Handler) -> Outcome:
    try:
        envelope = Envelope.model_validate(json.loads(body))
    except (json.JSONDecodeError, ValidationError) as exc:
        logger.error("Mensagem inválida descartada: %s", exc)
        record_event("MessageRejected", {"reason": str(exc)[:255]})
        return "invalid"

    tokens = (
        correlation_id_ctx.set(envelope.correlation_id),
        saga_id_ctx.set(envelope.saga_id),
        os_id_ctx.set(envelope.os_id),
    )
    try:
        with background_transaction(f"consume/{envelope.type}"):
            accept_trace_headers(envelope.trace)
            add_attributes(
                {
                    "saga_id": envelope.saga_id,
                    "os_id": envelope.os_id,
                    "message_type": envelope.type,
                }
            )
            outcome = handler(envelope)
            logger.info(
                "Mensagem %s: %s",
                envelope.type,
                outcome,
                extra={
                    "message_id": envelope.message_id,
                    "message_type": envelope.type,
                },
            )
            return outcome
    finally:
        correlation_id_ctx.reset(tokens[0])
        saga_id_ctx.reset(tokens[1])
        os_id_ctx.reset(tokens[2])


def poll_once(
    bus: SqsBus, queue: str, handler: Handler, wait_seconds: int | None = None
) -> int:
    handled = 0
    for raw in bus.receive(queue, wait_seconds=wait_seconds):
        try:
            handle_message(raw["Body"], handler)
            bus.delete(queue, raw["ReceiptHandle"])
            handled += 1
        except Exception as exc:
            logger.error(
                "Erro ao processar mensagem (ficará para retry/DLQ): %s",
                exc,
                exc_info=True,
                extra={"queue": queue},
            )
            record_event(
                "MessageProcessingFailure", {"queue": queue, "error": str(exc)[:255]}
            )
    return handled
