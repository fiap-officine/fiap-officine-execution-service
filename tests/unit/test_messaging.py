import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from app.config import Settings
from app.infrastructure.dynamo import (
    Dynamo,
    deserialize,
    get_dynamo,
    serialize,
    set_dynamo,
)
from app.messaging.consumer import handle_message, poll_once
from app.messaging.outbox import relay_pending
from app.services import execucao_service as svc
from app.workers import BackgroundWorkers, run_consumer_once, run_outbox_relay_once
from tests.conftest import drain_queue
from tests.helpers import comando, estoque, reservar, tipos_outbox


def test_relay_publica_e_apaga(dynamo, sqs_bus):
    estoque(dynamo, 1, 5)
    reservar()
    assert run_outbox_relay_once(dynamo, sqs_bus) == 1
    assert tipos_outbox(dynamo) == []
    mensagens = [json.loads(m["Body"]) for m in drain_queue(sqs_bus, "os-saga-replies")]
    assert [m["type"] for m in mensagens] == ["ExecucaoAgendada"]
    assert mensagens[0]["source"] == "execution-service"


def test_relay_mantem_item_se_publish_falhar(dynamo):
    reservar(pecas=[])
    bus = MagicMock()
    bus.send.side_effect = RuntimeError("SQS fora")
    assert relay_pending(dynamo, bus) == 0
    assert tipos_outbox(dynamo) == ["ExecucaoAgendada"]


def test_consumer_ponta_a_ponta(dynamo, sqs_bus):
    estoque(dynamo, 1, 5)
    env = comando("ReservarEEnfileirar", {"pecas": [{"peca_id": 1, "quantidade": 1}]})
    sqs_bus.send("execution-commands", env.model_dump_json(), {})
    sqs_bus.send("execution-commands", "lixo", {})
    assert run_consumer_once(sqs_bus, wait_seconds=0) == 2
    assert svc.buscar_meta(dynamo, 1)["status"] == "aguardando_execucao"


def test_poll_once_mantem_mensagem_quando_handler_falha(sqs_bus):
    sqs_bus.send("execution-commands", comando("X", {}).model_dump_json(), {})

    def explode(env):
        raise RuntimeError("erro")

    with patch("app.messaging.consumer.record_event") as rec:
        assert poll_once(sqs_bus, "execution-commands", explode, wait_seconds=0) == 0
    rec.assert_called_once()


def test_mensagem_invalida():
    assert handle_message(json.dumps({"a": 1}), lambda e: "processed") == "invalid"


def test_erro_inesperado_no_dynamo_propaga(dynamo):
    erro = ClientError(
        {"Error": {"Code": "ProvisionedThroughputExceededException"}},
        "TransactWriteItems",
    )
    with patch.object(dynamo.client, "transact_write_items", side_effect=erro):
        with pytest.raises(ClientError):
            reservar(pecas=[])
        estoque(dynamo, 1, 1)


def test_transacao_cancelada_sem_motivo_conhecido_propaga(dynamo):
    erro = ClientError(
        {
            "Error": {"Code": "TransactionCanceledException"},
            "CancellationReasons": [{"Code": "TransactionConflict"}] * 5,
        },
        "TransactWriteItems",
    )
    with patch.object(dynamo.client, "transact_write_items", side_effect=erro):
        with pytest.raises(ClientError):
            reservar()


def test_serializacao_dynamo():
    item = serialize({"a": 1, "b": 1.5, "c": None, "d": [{"x": 2}]})
    assert "c" not in item
    assert deserialize(item) == {"a": 1, "b": 1.5, "d": [{"x": 2}]}
    assert deserialize(None) is None
    assert deserialize({"n": {"N": "3"}}) == {"n": 3}
    assert isinstance(serialize({"v": Decimal("2")})["v"]["N"], str)


def test_nomes_de_tabelas_e_filas_sem_prefixo():
    s = Settings(DYNAMODB_TABLE_PREFIX="", SQS_QUEUE_PREFIX="")
    assert s.table_name("estoque") == "estoque"
    assert s.queue_name("fila") == "fila"


def test_get_dynamo_singleton():
    set_dynamo(None)
    assert get_dynamo() is get_dynamo()
    assert isinstance(get_dynamo(), Dynamo)
    set_dynamo(None)


def test_ensure_tables_idempotente(dynamo):
    dynamo.ensure_tables()
    assert dynamo.ping() is True


def test_background_workers():
    workers = BackgroundWorkers()
    with (
        patch("app.workers.run_consumer_once", side_effect=RuntimeError("x")),
        patch("app.workers.run_outbox_relay_once", return_value=0),
    ):
        workers.start()
        assert {t.name for t in workers._threads} == {
            "execution-consumer",
            "outbox-relay",
        }
        workers.stop(timeout=5)
    assert workers._threads == []


def test_lifespan_cria_tabelas_quando_configurado(dynamo):
    from fastapi.testclient import TestClient

    from app import main

    with (
        patch.object(main.settings, "DYNAMODB_CREATE_TABLES", True),
        patch.object(main.settings, "RUN_WORKERS", True),
        patch("app.workers.BackgroundWorkers") as workers,
    ):
        with TestClient(main.app):
            pass
    workers.return_value.start.assert_called_once()
    workers.return_value.stop.assert_called_once()
