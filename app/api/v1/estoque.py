from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import Operador, get_operador
from app.infrastructure.dynamo import get_dynamo
from app.services import estoque_service

router = APIRouter(prefix="/estoque", tags=["Estoque de peças"])


class EntradaEstoqueRequest(BaseModel):
    quantidade_entrada: int = Field(..., ge=0)
    estoque_minimo: int | None = Field(None, ge=0)
    nome: str | None = Field(None, max_length=255)


@router.get("/", response_model=list[dict[str, Any]])
def listar_estoque(_: Operador = Depends(get_operador)):
    return estoque_service.listar(get_dynamo())


@router.get("/{peca_id}", response_model=dict[str, Any])
def buscar_estoque(peca_id: int, _: Operador = Depends(get_operador)):
    item = estoque_service.buscar(get_dynamo(), peca_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Peça sem registro de estoque")
    return item


@router.put("/{peca_id}", response_model=dict[str, Any])
def registrar_entrada(
    peca_id: int, dados: EntradaEstoqueRequest, _: Operador = Depends(get_operador)
):
    """Entrada de estoque (soma ao disponível) e ajuste do estoque mínimo.
    O `peca_id` é o mesmo do catálogo do OS Service."""
    return estoque_service.registrar_entrada(
        get_dynamo(),
        peca_id,
        dados.quantidade_entrada,
        dados.estoque_minimo,
        dados.nome,
    )
