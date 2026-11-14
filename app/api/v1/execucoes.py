from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.deps import Operador, get_operador
from app.infrastructure.dynamo import get_dynamo
from app.services import execucao_service as svc

router = APIRouter(prefix="/execucoes", tags=["Execução (fila e mecânico)"])


class AcaoRequest(BaseModel):
    observacao: str | None = None


def _executar(os_id: int, acao: str, operador: Operador, dados: AcaoRequest | None):
    try:
        return svc.executar_acao(
            get_dynamo(),
            os_id,
            acao,
            operador.username,
            dados.observacao if dados else None,
        )
    except svc.ExecucaoError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/fila", response_model=list[dict[str, Any]])
def fila_de_execucao(
    status: Literal["aguardando_execucao", "em_diagnostico", "em_execucao"] = Query(
        "aguardando_execucao"
    ),
    _: Operador = Depends(get_operador),
):
    """Fila ordenada por prioridade e ordem de chegada (GSI `fila-por-status`)."""
    return svc.listar_fila(get_dynamo(), status)


@router.get("/{os_id}", response_model=dict[str, Any])
def detalhar_execucao(os_id: int, _: Operador = Depends(get_operador)):
    """Estado atual e linha do tempo da execução da OS."""
    try:
        return svc.detalhar(get_dynamo(), os_id)
    except svc.ExecucaoError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/{os_id}/iniciar-diagnostico", response_model=dict[str, Any])
def iniciar_diagnostico(
    os_id: int,
    dados: AcaoRequest | None = Body(None),
    operador: Operador = Depends(get_operador),
):
    """aguardando_execucao -> em_diagnostico (publica `DiagnosticoIniciado`)."""
    return _executar(os_id, "iniciar-diagnostico", operador, dados)


@router.post("/{os_id}/iniciar-reparo", response_model=dict[str, Any])
def iniciar_reparo(
    os_id: int,
    dados: AcaoRequest | None = Body(None),
    operador: Operador = Depends(get_operador),
):
    """em_diagnostico -> em_execucao (publica `ReparoIniciado`). **Pivô da saga**:
    a partir daqui a OS não pode mais ser compensada."""
    return _executar(os_id, "iniciar-reparo", operador, dados)


@router.post("/{os_id}/finalizar", response_model=dict[str, Any])
def finalizar(
    os_id: int,
    dados: AcaoRequest | None = Body(None),
    operador: Operador = Depends(get_operador),
):
    """em_execucao -> finalizada: baixa definitiva das peças reservadas, alerta de
    estoque mínimo e `ExecucaoFinalizada` para a saga."""
    return _executar(os_id, "finalizar", operador, dados)
