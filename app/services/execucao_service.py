"""Fila de execução, reserva de estoque e ações do mecânico (DynamoDB).

Toda mudança de estado + resposta para a saga acontece em um único
`TransactWriteItems` (estoque, META, linha do tempo, MSG# e OUTBOX#).
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from botocore.exceptions import ClientError

from app.config import get_settings
from app.infrastructure.dynamo import GSI1, Dynamo, deserialize, serialize, value
from app.messaging.consumer import Outcome
from app.messaging.envelope import Envelope, new_envelope
from app.messaging.outbox import outbox_put, processed_put
from app.messaging.tracing import record_event

logger = logging.getLogger(__name__)

# Comandos
RESERVAR_E_ENFILEIRAR = "ReservarEEnfileirar"
LIBERAR_RESERVA = "LiberarReserva"
# Respostas / eventos
EXECUCAO_AGENDADA = "ExecucaoAgendada"
RESERVA_FALHOU = "ReservaFalhou"
RESERVA_LIBERADA = "ReservaLiberada"
DIAGNOSTICO_INICIADO = "DiagnosticoIniciado"
REPARO_INICIADO = "ReparoIniciado"
EXECUCAO_FINALIZADA = "ExecucaoFinalizada"

AGUARDANDO = "aguardando_execucao"
EM_DIAGNOSTICO = "em_diagnostico"
EM_EXECUCAO = "em_execucao"
FINALIZADA = "finalizada"
CANCELADA = "cancelada"

PRIORIDADE = {"alta": 1, "normal": 2, "baixa": 3}

# ação do mecânico -> (status de origem, status destino, evento, campo de data)
ACOES: dict[str, tuple[str, str, str, str]] = {
    "iniciar-diagnostico": (
        AGUARDANDO,
        EM_DIAGNOSTICO,
        DIAGNOSTICO_INICIADO,
        "diagnostico_em",
    ),
    "iniciar-reparo": (EM_DIAGNOSTICO, EM_EXECUCAO, REPARO_INICIADO, "reparo_em"),
    "finalizar": (EM_EXECUCAO, FINALIZADA, EXECUCAO_FINALIZADA, "finalizado_em"),
}


class ExecucaoError(Exception):
    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _key_meta(os_id: int) -> dict[str, Any]:
    return {"PK": {"S": f"OS#{os_id}"}, "SK": {"S": "META"}}


def _evento(dynamo: Dynamo, os_id: int, tipo: str, **attrs) -> dict[str, Any]:
    agora = _now()
    return {
        "Put": {
            "TableName": dynamo.execucoes,
            "Item": serialize(
                {
                    "PK": f"OS#{os_id}",
                    "SK": f"EVT#{agora}#{uuid.uuid4().hex[:8]}",
                    "tipo": tipo,
                    "timestamp": agora,
                    **attrs,
                }
            ),
        }
    }


def _resposta(
    message_type: str,
    payload: dict,
    *,
    origem: Envelope | None = None,
    meta: dict | None = None,
    kind: str = "reply",
) -> Envelope:
    return new_envelope(
        message_type,
        kind,  # type: ignore[arg-type]
        payload,
        source=get_settings().SERVICE_NAME,
        saga_id=origem.saga_id if origem else (meta or {}).get("saga_id"),
        os_id=origem.os_id if origem else (meta or {}).get("os_id"),
        correlation_id=origem.correlation_id
        if origem
        else (meta or {}).get("correlation_id"),
    )


def _agrupar_pecas(pecas: list[dict] | None) -> list[dict]:
    total: dict[int, int] = {}
    for p in pecas or []:
        quantidade = int(p["quantidade"])
        if quantidade <= 0:
            continue
        total[int(p["peca_id"])] = total.get(int(p["peca_id"]), 0) + quantidade
    return [{"peca_id": k, "quantidade": v} for k, v in sorted(total.items())]


def _reasons(exc: ClientError) -> list[str]:
    return [r.get("Code", "None") for r in exc.response.get("CancellationReasons", [])]


def _is_canceled(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") == "TransactionCanceledException"


def _ja_processada(dynamo: Dynamo, message_id: str) -> bool:
    item = dynamo.client.get_item(
        TableName=dynamo.execucoes,
        Key={"PK": {"S": f"MSG#{message_id}"}, "SK": {"S": "MSG"}},
        ConsistentRead=True,
    ).get("Item")
    return item is not None


def buscar_meta(dynamo: Dynamo, os_id: int) -> dict | None:
    item = dynamo.client.get_item(
        TableName=dynamo.execucoes, Key=_key_meta(os_id), ConsistentRead=True
    ).get("Item")
    return deserialize(item)


def _responder_apenas(dynamo: Dynamo, origem: Envelope, envelope: Envelope) -> Outcome:
    """Grava MSG# + OUTBOX# (resposta sem efeito colateral)."""
    try:
        dynamo.client.transact_write_items(
            TransactItems=[processed_put(dynamo, origem), outbox_put(dynamo, envelope)]
        )
    except ClientError as exc:
        if _is_canceled(exc) and _reasons(exc)[:1] == ["ConditionalCheckFailed"]:
            return "duplicate"
        raise
    return "processed"


def _faltantes(dynamo: Dynamo, pecas: list[dict]) -> list[dict]:
    faltantes = []
    for p in pecas:
        item = deserialize(
            dynamo.client.get_item(
                TableName=dynamo.estoque,
                Key={"PK": {"S": f"PECA#{p['peca_id']}"}},
                ConsistentRead=True,
            ).get("Item")
        )
        disponivel = int(item.get("disponivel", 0)) if item else 0
        if disponivel < p["quantidade"]:
            faltantes.append(
                {
                    "peca_id": p["peca_id"],
                    "disponivel": disponivel,
                    "necessario": p["quantidade"],
                }
            )
    return faltantes


def contar_fila(dynamo: Dynamo, status: str = AGUARDANDO) -> int:
    return dynamo.client.query(
        TableName=dynamo.execucoes,
        IndexName=GSI1,
        KeyConditionExpression="GSI1PK = :p",
        ExpressionAttributeValues={":p": {"S": f"FILA#{status}"}},
        Select="COUNT",
    )["Count"]


# ── Comando: ReservarEEnfileirar (T5) ───────────
def reservar_e_enfileirar(dynamo: Dynamo, env: Envelope) -> Outcome:
    if _ja_processada(dynamo, env.message_id):
        return "duplicate"
    os_id = int(env.os_id)
    pecas = _agrupar_pecas(env.payload.get("pecas"))
    prioridade = env.payload.get("prioridade") or "normal"
    agora = _now()
    posicao = contar_fila(dynamo) + 1

    reservas = [
        {
            "Update": {
                "TableName": dynamo.estoque,
                "Key": {"PK": {"S": f"PECA#{p['peca_id']}"}},
                "UpdateExpression": "SET disponivel = disponivel - :q, reservada = reservada + :q",
                "ConditionExpression": "attribute_exists(PK) AND disponivel >= :q",
                "ExpressionAttributeValues": {":q": value(p["quantidade"])},
            }
        }
        for p in pecas
    ]
    meta = {
        "PK": f"OS#{os_id}",
        "SK": "META",
        "os_id": os_id,
        "saga_id": env.saga_id,
        "correlation_id": env.correlation_id,
        "status": AGUARDANDO,
        "prioridade": prioridade,
        "pecas": pecas,
        "servicos": env.payload.get("servicos") or [],
        "enfileirado_em": agora,
        "GSI1PK": f"FILA#{AGUARDANDO}",
        "GSI1SK": f"{PRIORIDADE.get(prioridade, 2)}#{agora}#{os_id:010d}",
    }
    resposta = _resposta(EXECUCAO_AGENDADA, {"posicao_fila": posicao}, origem=env)
    itens = reservas + [
        {
            "Put": {
                "TableName": dynamo.execucoes,
                "Item": serialize(meta),
                "ConditionExpression": "attribute_not_exists(PK)",
            }
        },
        _evento(dynamo, os_id, "enfileirada", pecas=pecas, posicao_fila=posicao),
        processed_put(dynamo, env),
        outbox_put(dynamo, resposta),
    ]
    try:
        dynamo.client.transact_write_items(TransactItems=itens)
    except ClientError as exc:
        if not _is_canceled(exc):
            raise
        reasons = _reasons(exc)
        n = len(pecas)
        if len(reasons) > n + 2 and reasons[n + 2] == "ConditionalCheckFailed":
            return "duplicate"
        if len(reasons) > n and reasons[n] == "ConditionalCheckFailed":
            # Execução já agendada por outra mensagem: reenvia a confirmação.
            return _responder_apenas(
                dynamo,
                env,
                _resposta(EXECUCAO_AGENDADA, {"posicao_fila": None}, origem=env),
            )
        if "ConditionalCheckFailed" in reasons[:n]:
            faltantes = _faltantes(dynamo, pecas)
            logger.warning("Estoque insuficiente para OS %s: %s", os_id, faltantes)
            record_event(
                "ReservaFalhouEvent", {"os_id": os_id, "faltantes": len(faltantes)}
            )
            return _responder_apenas(
                dynamo,
                env,
                _resposta(RESERVA_FALHOU, {"faltantes": faltantes}, origem=env),
            )
        raise
    record_event(
        "ExecucaoEvent", {"action": "agendada", "os_id": os_id, "posicao": posicao}
    )
    return "processed"


# ── Compensação: LiberarReserva (C5) ────────────
def liberar_reserva(dynamo: Dynamo, env: Envelope) -> Outcome:
    if _ja_processada(dynamo, env.message_id):
        return "duplicate"
    os_id = int(env.os_id)
    meta = buscar_meta(dynamo, os_id)
    if meta is None or meta["status"] not in (AGUARDANDO, EM_DIAGNOSTICO):
        payload = {"liberada": False, "status": meta["status"] if meta else None}
        return _responder_apenas(
            dynamo, env, _resposta(RESERVA_LIBERADA, payload, origem=env)
        )

    devolucoes = [
        {
            "Update": {
                "TableName": dynamo.estoque,
                "Key": {"PK": {"S": f"PECA#{p['peca_id']}"}},
                "UpdateExpression": "SET disponivel = disponivel + :q, reservada = reservada - :q",
                "ExpressionAttributeValues": {":q": value(p["quantidade"])},
            }
        }
        for p in meta.get("pecas", [])
    ]
    itens = devolucoes + [
        {
            "Update": {
                "TableName": dynamo.execucoes,
                "Key": _key_meta(os_id),
                "UpdateExpression": "SET #s = :cancelada, cancelado_em = :agora REMOVE GSI1PK, GSI1SK",
                "ConditionExpression": "#s = :atual",
                "ExpressionAttributeNames": {"#s": "status"},
                "ExpressionAttributeValues": {
                    ":cancelada": value(CANCELADA),
                    ":agora": value(_now()),
                    ":atual": value(meta["status"]),
                },
            }
        },
        _evento(dynamo, os_id, "reserva_liberada", motivo=env.payload.get("motivo")),
        processed_put(dynamo, env),
        outbox_put(dynamo, _resposta(RESERVA_LIBERADA, {"liberada": True}, origem=env)),
    ]
    try:
        dynamo.client.transact_write_items(TransactItems=itens)
    except ClientError as exc:
        n = len(devolucoes)
        if _is_canceled(exc) and _reasons(exc)[n + 2 : n + 3] == [
            "ConditionalCheckFailed"
        ]:
            return "duplicate"
        raise  # status mudou concorrentemente: SQS reentrega e reavalia
    record_event("ExecucaoEvent", {"action": "reserva_liberada", "os_id": os_id})
    return "processed"


# ── Ações do mecânico (REST) ────────────────────
def executar_acao(
    dynamo: Dynamo, os_id: int, acao: str, mecanico: str, observacao: str | None
) -> dict:
    origem, destino, evento, campo_data = ACOES[acao]
    meta = buscar_meta(dynamo, os_id)
    if meta is None:
        raise ExecucaoError("Execução não encontrada para esta OS", 404)
    if meta["status"] != origem:
        raise ExecucaoError(
            f"Ação '{acao}' exige status '{origem}', mas a execução está em '{meta['status']}'"
        )
    agora = _now()
    if destino == FINALIZADA:
        update = f"SET #s = :destino, {campo_data} = :agora, mecanico = :mec REMOVE GSI1PK, GSI1SK"
        values = {}
    else:
        update = (
            f"SET #s = :destino, {campo_data} = :agora, mecanico = :mec, GSI1PK = :fila"
        )
        values = {":fila": value(f"FILA#{destino}")}
    itens: list[dict] = [
        {
            "Update": {
                "TableName": dynamo.execucoes,
                "Key": _key_meta(os_id),
                "UpdateExpression": update,
                "ConditionExpression": "#s = :origem",
                "ExpressionAttributeNames": {"#s": "status"},
                "ExpressionAttributeValues": {
                    ":destino": value(destino),
                    ":origem": value(origem),
                    ":agora": value(agora),
                    ":mec": value(mecanico),
                    **values,
                },
            }
        }
    ]
    if destino == FINALIZADA:
        # Baixa definitiva: as peças reservadas foram consumidas no reparo.
        itens += [
            {
                "Update": {
                    "TableName": dynamo.estoque,
                    "Key": {"PK": {"S": f"PECA#{p['peca_id']}"}},
                    "UpdateExpression": "SET reservada = reservada - :q",
                    "ExpressionAttributeValues": {":q": value(p["quantidade"])},
                }
            }
            for p in meta.get("pecas", [])
        ]
    payload = {"mecanico": mecanico, "observacao": observacao, "timestamp": agora}
    itens += [
        _evento(dynamo, os_id, destino, mecanico=mecanico, observacao=observacao),
        outbox_put(dynamo, _resposta(evento, payload, meta=meta, kind="event")),
    ]
    try:
        dynamo.client.transact_write_items(TransactItems=itens)
    except ClientError as exc:
        if _is_canceled(exc):
            raise ExecucaoError(
                "A execução foi alterada por outra operação; tente novamente"
            ) from exc
        raise
    record_event(
        "ExecucaoEvent", {"action": destino, "os_id": os_id, "mecanico": mecanico}
    )
    if destino == FINALIZADA:
        from app.services.estoque_service import verificar_alertas

        verificar_alertas(dynamo, [p["peca_id"] for p in meta.get("pecas", [])])
    return buscar_meta(dynamo, os_id)


# ── Consultas ───────────────────────────────────
def listar_fila(dynamo: Dynamo, status: str = AGUARDANDO) -> list[dict]:
    response = dynamo.client.query(
        TableName=dynamo.execucoes,
        IndexName=GSI1,
        KeyConditionExpression="GSI1PK = :p",
        ExpressionAttributeValues={":p": {"S": f"FILA#{status}"}},
        ScanIndexForward=True,
    )
    return [deserialize(i) for i in response.get("Items", [])]


def detalhar(dynamo: Dynamo, os_id: int) -> dict:
    response = dynamo.client.query(
        TableName=dynamo.execucoes,
        KeyConditionExpression="PK = :pk",
        ExpressionAttributeValues={":pk": {"S": f"OS#{os_id}"}},
    )
    itens = [deserialize(i) for i in response.get("Items", [])]
    meta = next((i for i in itens if i["SK"] == "META"), None)
    if meta is None:
        raise ExecucaoError("Execução não encontrada para esta OS", 404)
    eventos = sorted(
        (i for i in itens if i["SK"].startswith("EVT#")), key=lambda e: e["SK"]
    )
    return {"execucao": meta, "linha_do_tempo": eventos}
