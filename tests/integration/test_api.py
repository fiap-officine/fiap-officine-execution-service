"""API REST do Execution: estoque, fila e ações do mecânico."""

from unittest.mock import patch

from tests.conftest import make_token
from tests.helpers import estoque, outbox, reservar, tipos_outbox


class TestAutenticacao:
    def test_requer_token_de_operador(self, client):
        assert client.get("/api/v1/estoque/").status_code == 401
        invalido = {"Authorization": "Bearer abc"}
        assert client.get("/api/v1/estoque/", headers=invalido).status_code == 401
        cliente = {"Authorization": f"Bearer {make_token('cliente', '52998224725')}"}
        assert client.get("/api/v1/estoque/", headers=cliente).status_code == 403


class TestEstoqueAPI:
    def test_entrada_consulta_e_listagem(self, client, headers):
        resp = client.put(
            "/api/v1/estoque/1",
            json={"quantidade_entrada": 5, "estoque_minimo": 2, "nome": "Pastilha"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "peca_id": 1,
            "nome": "Pastilha",
            "disponivel": 5,
            "reservada": 0,
            "estoque_minimo": 2,
            "updated_at": resp.json()["updated_at"],
        }
        resp = client.put(
            "/api/v1/estoque/1", json={"quantidade_entrada": 3}, headers=headers
        )
        assert resp.json()["disponivel"] == 8
        assert resp.json()["estoque_minimo"] == 2
        client.put("/api/v1/estoque/2", json={"quantidade_entrada": 1}, headers=headers)

        assert (
            client.get("/api/v1/estoque/1", headers=headers).json()["disponivel"] == 8
        )
        assert [
            i["peca_id"] for i in client.get("/api/v1/estoque/", headers=headers).json()
        ] == [1, 2]
        assert client.get("/api/v1/estoque/99", headers=headers).status_code == 404
        invalido = client.put(
            "/api/v1/estoque/1", json={"quantidade_entrada": -1}, headers=headers
        )
        assert invalido.status_code == 422


class TestFilaEMecanico:
    def test_fila_ordenada_por_prioridade(self, client, headers, dynamo):
        estoque(dynamo, 1, 10)
        reservar(os_id=1, prioridade="baixa")
        reservar(os_id=2, prioridade="normal")
        reservar(os_id=3, prioridade="alta")
        fila = client.get("/api/v1/execucoes/fila", headers=headers).json()
        assert [e["os_id"] for e in fila] == [3, 2, 1]

    def test_fluxo_completo_do_mecanico(self, client, headers, dynamo):
        estoque(dynamo, 1, 3, minimo=1)
        reservar(pecas=[{"peca_id": 1, "quantidade": 2}])

        resp = client.post(
            "/api/v1/execucoes/1/iniciar-diagnostico",
            json={"observacao": "Barulho no freio"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "em_diagnostico"
        assert resp.json()["mecanico"] == "mecanico.ze"
        fila = client.get(
            "/api/v1/execucoes/fila?status=em_diagnostico", headers=headers
        )
        assert [e["os_id"] for e in fila.json()] == [1]

        assert (
            client.post("/api/v1/execucoes/1/iniciar-reparo", headers=headers).json()[
                "status"
            ]
            == "em_execucao"
        )

        with patch("app.services.estoque_service.record_event") as alerta:
            resp = client.post(
                "/api/v1/execucoes/1/finalizar",
                json={"observacao": "Pastilhas trocadas"},
                headers=headers,
            )
        assert resp.json()["status"] == "finalizada"
        alerta.assert_called_once()  # disponível 1 <= mínimo 1
        estoque_final = client.get("/api/v1/estoque/1", headers=headers).json()
        assert (estoque_final["disponivel"], estoque_final["reservada"]) == (1, 0)

        assert tipos_outbox(dynamo) == [
            "ExecucaoAgendada",
            "DiagnosticoIniciado",
            "ReparoIniciado",
            "ExecucaoFinalizada",
        ]
        finalizada = outbox(dynamo)[-1]
        assert finalizada["kind"] == "event"
        assert finalizada["saga_id"] == "saga-1"
        assert finalizada["payload"]["observacao"] == "Pastilhas trocadas"

        detalhe = client.get("/api/v1/execucoes/1", headers=headers).json()
        assert detalhe["execucao"]["status"] == "finalizada"
        assert [e["tipo"] for e in detalhe["linha_do_tempo"]] == [
            "enfileirada",
            "em_diagnostico",
            "em_execucao",
            "finalizada",
        ]

    def test_acao_fora_de_ordem_retorna_409(self, client, headers, dynamo):
        reservar(pecas=[])
        resp = client.post("/api/v1/execucoes/1/finalizar", headers=headers)
        assert resp.status_code == 409
        assert "exige status 'em_execucao'" in resp.json()["detail"]

    def test_execucao_inexistente(self, client, headers):
        assert client.get("/api/v1/execucoes/999", headers=headers).status_code == 404
        assert (
            client.post(
                "/api/v1/execucoes/999/iniciar-diagnostico", headers=headers
            ).status_code
            == 404
        )

    def test_alteracao_concorrente_retorna_409(self, client, headers, dynamo):
        from botocore.exceptions import ClientError

        reservar(pecas=[])
        erro = ClientError(
            {
                "Error": {"Code": "TransactionCanceledException"},
                "CancellationReasons": [{"Code": "ConditionalCheckFailed"}],
            },
            "TransactWriteItems",
        )
        with patch.object(dynamo.client, "transact_write_items", side_effect=erro):
            resp = client.post(
                "/api/v1/execucoes/1/iniciar-diagnostico", headers=headers
            )
        assert resp.status_code == 409


class TestHealth:
    def test_ok(self, client):
        resp = client.get("/execution/health")
        assert resp.status_code == 200
        assert resp.json()["services"]["database"]["type"] == "DynamoDB"
        assert resp.json()["services"]["messaging"]["status"] == "up"
        assert client.get("/").json()["service"] == "execution-service"

    def test_dynamo_fora(self, client):
        with (
            patch("app.api.v1.health.get_dynamo") as dyn,
            patch("app.api.v1.health.get_bus") as bus,
        ):
            dyn.return_value.ping.side_effect = Exception("down")
            bus.return_value.ping.side_effect = Exception("down")
            resp = client.get("/health")
        assert resp.status_code == 503
        assert resp.json()["services"]["messaging"]["status"] == "down"
