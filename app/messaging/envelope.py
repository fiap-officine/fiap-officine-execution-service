"""Envelope padrão das mensagens trocadas entre os microsserviços (PLANO §5.2)."""

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.logging import get_correlation_id

MessageKind = Literal["command", "reply", "event"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Envelope(BaseModel):
    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: str
    kind: MessageKind
    saga_id: str | None = None
    os_id: int | None = None
    correlation_id: str | None = None
    occurred_at: str = Field(default_factory=_now_iso)
    source: str
    version: int = 1
    trace: dict[str, str] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)


def new_envelope(
    message_type: str,
    kind: MessageKind,
    payload: dict[str, Any],
    *,
    source: str,
    saga_id: str | None = None,
    os_id: int | None = None,
    correlation_id: str | None = None,
    trace: dict[str, str] | None = None,
) -> Envelope:
    return Envelope(
        type=message_type,
        kind=kind,
        payload=payload,
        source=source,
        saga_id=saga_id,
        os_id=os_id,
        correlation_id=correlation_id or get_correlation_id() or str(uuid.uuid4()),
        trace=trace or {},
    )
