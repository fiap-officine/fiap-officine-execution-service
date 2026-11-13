"""Outbox no DynamoDB.

As respostas para o orquestrador são gravadas como itens `OUTBOX#<message_id>`
dentro do MESMO `TransactWriteItems` que altera estoque/fila. Um relay lê o
índice esparso (GSI1PK = "OUTBOX"), publica no SQS e apaga o item. Se o
processo cair após o commit, a mensagem continua lá e é publicada depois.
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from app.config import get_settings
from app.infrastructure.dynamo import GSI1, Dynamo, serialize
from app.messaging.envelope import Envelope
from app.messaging.sqs import SqsBus
from app.messaging.tracing import inject_trace_headers

logger = logging.getLogger(__name__)

OS_REPLIES = "os-saga-replies"
OUTBOX_KEY = "OUTBOX"


def outbox_put(dynamo: Dynamo, envelope: Envelope) -> dict[str, Any]:
    if not envelope.trace:
        envelope.trace = inject_trace_headers()
    return {
        "Put": {
            "TableName": dynamo.execucoes,
            "Item": serialize(
                {
                    "PK": f"OUTBOX#{envelope.message_id}",
                    "SK": OUTBOX_KEY,
                    "GSI1PK": OUTBOX_KEY,
                    "GSI1SK": f"{envelope.occurred_at}#{envelope.message_id}",
                    "message_type": envelope.type,
                    "envelope": envelope.model_dump_json(),
                }
            ),
            "ConditionExpression": "attribute_not_exists(PK)",
        }
    }


def processed_put(dynamo: Dynamo, envelope: Envelope) -> dict[str, Any]:
    """Marca a mensagem consumida como processada (idempotência, com TTL)."""
    ttl = int(time.time()) + get_settings().MSG_TTL_DAYS * 86400
    return {
        "Put": {
            "TableName": dynamo.execucoes,
            "Item": serialize(
                {
                    "PK": f"MSG#{envelope.message_id}",
                    "SK": "MSG",
                    "message_type": envelope.type,
                    "processed_at": datetime.now(timezone.utc).isoformat(),
                    "ttl": ttl,
                }
            ),
            "ConditionExpression": "attribute_not_exists(PK)",
        }
    }


def relay_pending(dynamo: Dynamo, bus: SqsBus, limit: int = 25) -> int:
    response = dynamo.client.query(
        TableName=dynamo.execucoes,
        IndexName=GSI1,
        KeyConditionExpression="GSI1PK = :o",
        ExpressionAttributeValues={":o": {"S": OUTBOX_KEY}},
        Limit=limit,
    )
    sent = 0
    for item in response.get("Items", []):
        body = item["envelope"]["S"]
        envelope = json.loads(body)
        try:
            bus.send(
                OS_REPLIES,
                body,
                {
                    "type": envelope.get("type"),
                    "message_id": envelope.get("message_id"),
                    "saga_id": envelope.get("saga_id"),
                    "source": envelope.get("source"),
                },
            )
        except Exception as exc:
            logger.warning("Falha ao publicar %s: %s", envelope.get("type"), exc)
            continue
        dynamo.client.delete_item(
            TableName=dynamo.execucoes, Key={"PK": item["PK"], "SK": item["SK"]}
        )
        sent += 1
        logger.info(
            "Mensagem %s publicada",
            envelope.get("type"),
            extra={
                "message_id": envelope.get("message_id"),
                "message_type": envelope.get("type"),
                "queue": OS_REPLIES,
                "saga_id": envelope.get("saga_id"),
                "os_id": envelope.get("os_id"),
            },
        )
    return sent
