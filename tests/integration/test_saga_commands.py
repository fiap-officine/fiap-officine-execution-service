"""Comandos da saga no Execution: ReservarEEnfileirar e LiberarReserva."""

from app.services import estoque_service
from app.services import execucao_service as svc
from tests.helpers import (
    comando,
    estoque,
    liberar,
    outbox,
    processar,
    reservar,
    tipos_outbox,
)


class TestReservarEEnfileirar:
    def test_reserva_atomica_e_agenda(self, dynamo):
        estoque(dynamo, 1, 5)
        resultado, env = reservar()
        assert resultado == "processed"

        item = estoque_service.buscar(dynamo, 1)
        assert (item["disponivel"], item["reservada"]) == (3, 2)

        meta = svc.buscar_meta(dynamo, 1)
        assert meta["status"] == "aguardando_execucao"
        assert meta["saga_id"] == "saga-1"
        assert meta["pecas"] == [{"peca_id": 1, "quantidade": 2}]

        resposta = outbox(dynamo)[-1]
        assert resposta["type"] == "ExecucaoAgendada"
        assert resposta["saga_id"] == env.saga_id
        assert resposta["correlation_id"] == "corr-1"
        assert resposta["payload"]["posicao_fila"] == 1

    def test_estoque_insuficiente_responde_reserva_falhou(self, dynamo):
        estoque(dynamo, 1, 5)
        estoque(dynamo, 2, 1)
        resultado, _ = reservar(
            pecas=[
                {"peca_id": 1, "quantidade": 2},
                {"peca_id": 2, "quantidade": 3},
            ]
        )
        assert resultado == "processed"
        resposta = outbox(dynamo)[-1]
        assert resposta["type"] == "ReservaFalhou"
        assert resposta["payload"]["faltantes"] == [
            {"peca_id": 2, "disponivel": 1, "necessario": 3}
        ]
        # Nada foi reservado (transação atômica)
        assert estoque_service.buscar(dynamo, 1)["disponivel"] == 5
        assert svc.buscar_meta(dynamo, 1) is None

    def test_peca_sem_registro_de_estoque(self, dynamo):
        reservar(pecas=[{"peca_id": 77, "quantidade": 1}])
        resposta = outbox(dynamo)[-1]
        assert resposta["type"] == "ReservaFalhou"
        assert resposta["payload"]["faltantes"][0]["disponivel"] == 0

    def test_mensagem_duplicada_nao_reserva_duas_vezes(self, dynamo):
        estoque(dynamo, 1, 5)
        _, env = reservar()
        assert processar(env) == "duplicate"
        assert estoque_service.buscar(dynamo, 1)["disponivel"] == 3
        assert tipos_outbox(dynamo) == ["ExecucaoAgendada"]

    def test_segundo_comando_para_mesma_os_reenvia_confirmacao(self, dynamo):
        estoque(dynamo, 1, 5)
        reservar()
        resultado, _ = reservar()  # novo message_id, mesma OS
        assert resultado == "processed"
        assert estoque_service.buscar(dynamo, 1)["disponivel"] == 3
        assert tipos_outbox(dynamo) == ["ExecucaoAgendada", "ExecucaoAgendada"]

    def test_quantidades_da_mesma_peca_sao_somadas(self, dynamo):
        estoque(dynamo, 1, 5)
        reservar(
            pecas=[
                {"peca_id": 1, "quantidade": 1},
                {"peca_id": 1, "quantidade": 2},
                {"peca_id": 1, "quantidade": 0},
            ]
        )
        assert estoque_service.buscar(dynamo, 1)["reservada"] == 3

    def test_os_so_com_servicos(self, dynamo):
        resultado, _ = reservar(pecas=[])
        assert resultado == "processed"
        assert tipos_outbox(dynamo) == ["ExecucaoAgendada"]

    def test_posicao_na_fila(self, dynamo):
        estoque(dynamo, 1, 10)
        reservar(os_id=1)
        reservar(os_id=2)
        assert outbox(dynamo)[-1]["payload"]["posicao_fila"] == 2


class TestLiberarReserva:
    def test_devolve_estoque_e_sai_da_fila(self, dynamo):
        estoque(dynamo, 1, 5)
        reservar()
        resultado, _ = liberar()
        assert resultado == "processed"
        item = estoque_service.buscar(dynamo, 1)
        assert (item["disponivel"], item["reservada"]) == (5, 0)
        assert svc.buscar_meta(dynamo, 1)["status"] == "cancelada"
        assert svc.listar_fila(dynamo) == []
        resposta = outbox(dynamo)[-1]
        assert resposta["type"] == "ReservaLiberada"
        assert resposta["payload"]["liberada"] is True

    def test_sem_reserva_responde_noop(self, dynamo):
        liberar(os_id=404)
        resposta = outbox(dynamo)[-1]
        assert resposta["type"] == "ReservaLiberada"
        assert resposta["payload"] == {"liberada": False, "status": None}

    def test_apos_inicio_do_reparo_nao_libera(self, dynamo):
        estoque(dynamo, 1, 5)
        reservar()
        svc.executar_acao(dynamo, 1, "iniciar-diagnostico", "ze", None)
        svc.executar_acao(dynamo, 1, "iniciar-reparo", "ze", None)
        liberar()
        assert outbox(dynamo)[-1]["payload"]["liberada"] is False
        assert estoque_service.buscar(dynamo, 1)["reservada"] == 2

    def test_libera_durante_diagnostico(self, dynamo):
        estoque(dynamo, 1, 5)
        reservar()
        svc.executar_acao(dynamo, 1, "iniciar-diagnostico", "ze", None)
        liberar()
        assert estoque_service.buscar(dynamo, 1)["disponivel"] == 5

    def test_duplicada(self, dynamo):
        estoque(dynamo, 1, 5)
        reservar()
        _, env = liberar()
        assert processar(env) == "duplicate"
        _, env_noop = liberar(os_id=999)
        assert processar(env_noop) == "duplicate"


def test_comando_desconhecido(dynamo):
    assert processar(comando("Desconhecido", {})) == "invalid"
