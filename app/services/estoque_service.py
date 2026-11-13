"""Saldo de estoque de peças (tabela DynamoDB `estoque`)."""

import logging
from datetime import datetime, timezone

from app.infrastructure.dynamo import Dynamo, deserialize, value
from app.messaging.tracing import record_event

logger = logging.getLogger(__name__)


def _key(peca_id: int) -> dict:
    return {"PK": {"S": f"PECA#{peca_id}"}}


def _formatar(item: dict | None) -> dict | None:
    if item is None:
        return None
    return {
        "peca_id": item["peca_id"],
        "nome": item.get("nome"),
        "disponivel": item.get("disponivel", 0),
        "reservada": item.get("reservada", 0),
        "estoque_minimo": item.get("estoque_minimo", 0),
        "updated_at": item.get("updated_at"),
    }


def registrar_entrada(
    dynamo: Dynamo,
    peca_id: int,
    quantidade_entrada: int,
    estoque_minimo: int | None = None,
    nome: str | None = None,
) -> dict:
    sets = [
        "peca_id = :id",
        "reservada = if_not_exists(reservada, :zero)",
        "updated_at = :agora",
    ]
    values = {
        ":id": value(peca_id),
        ":zero": value(0),
        ":q": value(quantidade_entrada),
        ":agora": value(datetime.now(timezone.utc).isoformat()),
    }
    if estoque_minimo is not None:
        sets.append("estoque_minimo = :min")
        values[":min"] = value(estoque_minimo)
    else:
        sets.append("estoque_minimo = if_not_exists(estoque_minimo, :zero)")
    if nome:
        sets.append("nome = :nome")
        values[":nome"] = value(nome)
    response = dynamo.client.update_item(
        TableName=dynamo.estoque,
        Key=_key(peca_id),
        UpdateExpression=f"SET {', '.join(sets)} ADD disponivel :q",
        ExpressionAttributeValues=values,
        ReturnValues="ALL_NEW",
    )
    return _formatar(deserialize(response["Attributes"]))


def buscar(dynamo: Dynamo, peca_id: int) -> dict | None:
    item = dynamo.client.get_item(TableName=dynamo.estoque, Key=_key(peca_id)).get(
        "Item"
    )
    return _formatar(deserialize(item))


def listar(dynamo: Dynamo) -> list[dict]:
    items: list[dict] = []
    kwargs: dict = {"TableName": dynamo.estoque}
    while True:
        response = dynamo.client.scan(**kwargs)
        items += [_formatar(deserialize(i)) for i in response.get("Items", [])]
        if "LastEvaluatedKey" not in response:
            break
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
    return sorted(items, key=lambda i: i["peca_id"])


def verificar_alertas(dynamo: Dynamo, pecas: list[int]) -> list[dict]:
    """Emite AlertaDeEstoque quando o saldo disponível atinge o mínimo."""
    alertas = []
    for peca_id in pecas:
        item = buscar(dynamo, peca_id)
        if item and item["disponivel"] <= item["estoque_minimo"]:
            alertas.append(item)
            logger.warning(
                "Alerta de estoque: peça %s com %s unidades (mínimo %s)",
                peca_id,
                item["disponivel"],
                item["estoque_minimo"],
            )
            record_event(
                "AlertaDeEstoque",
                {
                    "peca_id": peca_id,
                    "disponivel": item["disponivel"],
                    "estoque_minimo": item["estoque_minimo"],
                },
            )
    return alertas
