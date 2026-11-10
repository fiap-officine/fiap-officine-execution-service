"""Acesso ao DynamoDB.

Tabela `execucoes` (single-table):

| PK               | SK                    | Uso                                         |
|------------------|-----------------------|---------------------------------------------|
| OS#<os_id>       | META                  | estado atual da execução (+ GSI1 = fila)    |
| OS#<os_id>       | EVT#<ts>#<id>         | linha do tempo                              |
| MSG#<message_id> | MSG                   | idempotência do consumer (TTL 7 dias)       |
| OUTBOX#<msg_id>  | OUTBOX                | respostas a publicar (GSI1PK = "OUTBOX")    |

GSI1 `fila-por-status`: GSI1PK = FILA#<status>, GSI1SK = <prioridade>#<enfileirado_em>#<os_id>.

Tabela `estoque`: PK = PECA#<peca_id>; atributos disponivel, reservada, estoque_minimo.
"""

import threading
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

from app.config import Settings, get_settings

EXECUCOES = "execucoes"
ESTOQUE = "estoque"
GSI1 = "fila-por-status"

_serializer = TypeSerializer()
_deserializer = TypeDeserializer()


def _to_dynamo(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_dynamo(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_to_dynamo(v) for v in value]
    return value


def _from_dynamo(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: _from_dynamo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_from_dynamo(v) for v in value]
    return value


def serialize(item: dict[str, Any]) -> dict[str, Any]:
    return {k: _serializer.serialize(v) for k, v in _to_dynamo(item).items()}


def deserialize(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if item is None:
        return None
    return _from_dynamo({k: _deserializer.deserialize(v) for k, v in item.items()})


def value(v: Any) -> dict[str, Any]:
    return _serializer.serialize(_to_dynamo(v))


class Dynamo:
    def __init__(self, settings: Settings | None = None, client: Any | None = None):
        self.settings = settings or get_settings()
        self._client = client
        self._lock = threading.Lock()

    @property
    def client(self):
        if self._client is None:
            with self._lock:
                if self._client is None:
                    self._client = boto3.client(
                        "dynamodb",
                        region_name=self.settings.AWS_REGION,
                        endpoint_url=self.settings.AWS_ENDPOINT_URL,
                    )
        return self._client

    @property
    def execucoes(self) -> str:
        return self.settings.table_name(EXECUCOES)

    @property
    def estoque(self) -> str:
        return self.settings.table_name(ESTOQUE)

    def ensure_tables(self) -> None:
        existentes = set(self.client.list_tables().get("TableNames", []))
        if self.execucoes not in existentes:
            self.client.create_table(
                TableName=self.execucoes,
                BillingMode="PAY_PER_REQUEST",
                AttributeDefinitions=[
                    {"AttributeName": n, "AttributeType": "S"}
                    for n in ("PK", "SK", "GSI1PK", "GSI1SK")
                ],
                KeySchema=[
                    {"AttributeName": "PK", "KeyType": "HASH"},
                    {"AttributeName": "SK", "KeyType": "RANGE"},
                ],
                GlobalSecondaryIndexes=[
                    {
                        "IndexName": GSI1,
                        "KeySchema": [
                            {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                            {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                        ],
                        "Projection": {"ProjectionType": "ALL"},
                    }
                ],
            )
        if self.estoque not in existentes:
            self.client.create_table(
                TableName=self.estoque,
                BillingMode="PAY_PER_REQUEST",
                AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"}],
                KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}],
            )

    def ping(self) -> bool:
        self.client.describe_table(TableName=self.execucoes)
        self.client.describe_table(TableName=self.estoque)
        return True


_dynamo: Dynamo | None = None


def get_dynamo() -> Dynamo:
    global _dynamo
    if _dynamo is None:
        _dynamo = Dynamo()
    return _dynamo


def set_dynamo(dynamo: Dynamo | None) -> None:
    global _dynamo
    _dynamo = dynamo
