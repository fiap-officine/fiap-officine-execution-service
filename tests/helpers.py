import json
import uuid

from app.handlers import execution_command_handler
from app.infrastructure.dynamo import GSI1, Dynamo
from app.messaging.consumer import handle_message
from app.messaging.envelope import Envelope, new_envelope
from app.services import estoque_service


def comando(tipo: str, payload: dict, os_id: int = 1) -> Envelope:
    return new_envelope(
        tipo,
        "command",
        payload,
        source="os-service",
        saga_id=f"saga-{os_id}",
        os_id=os_id,
        correlation_id=f"corr-{os_id}",
    )


def processar(envelope: Envelope) -> str:
    return handle_message(envelope.model_dump_json(), execution_command_handler)


def reservar(
    os_id: int = 1, pecas=None, prioridade: str = "normal"
) -> tuple[str, Envelope]:
    env = comando(
        "ReservarEEnfileirar",
        {
            "pecas": pecas if pecas is not None else [{"peca_id": 1, "quantidade": 2}],
            "servicos": [
                {"servico_id": 9, "quantidade": 1, "tempo_estimado_minutos": 30}
            ],
            "prioridade": prioridade,
        },
        os_id,
    )
    return processar(env), env


def liberar(os_id: int = 1) -> tuple[str, Envelope]:
    env = comando("LiberarReserva", {"motivo": "cancelada"}, os_id)
    return processar(env), env


def estoque(dynamo: Dynamo, peca_id: int, qtd: int, minimo: int = 0) -> None:
    estoque_service.registrar_entrada(dynamo, peca_id, qtd, minimo, f"Peça {peca_id}")


def outbox(dynamo: Dynamo) -> list[dict]:
    items = dynamo.client.query(
        TableName=dynamo.execucoes,
        IndexName=GSI1,
        KeyConditionExpression="GSI1PK = :o",
        ExpressionAttributeValues={":o": {"S": "OUTBOX"}},
    )["Items"]
    envelopes = [json.loads(i["envelope"]["S"]) for i in items]
    return sorted(envelopes, key=lambda e: e["occurred_at"])


def tipos_outbox(dynamo: Dynamo) -> list[str]:
    return [e["type"] for e in outbox(dynamo)]


def novo_id() -> str:
    return str(uuid.uuid4())
