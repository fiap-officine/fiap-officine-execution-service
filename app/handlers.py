"""Roteamento dos comandos da fila execution-commands."""

import logging

from app.infrastructure.dynamo import get_dynamo
from app.messaging.consumer import Outcome
from app.messaging.envelope import Envelope
from app.services import execucao_service as svc

logger = logging.getLogger(__name__)

HANDLERS = {
    svc.RESERVAR_E_ENFILEIRAR: svc.reservar_e_enfileirar,
    svc.LIBERAR_RESERVA: svc.liberar_reserva,
}


def execution_command_handler(envelope: Envelope) -> Outcome:
    handler = HANDLERS.get(envelope.type)
    if handler is None:
        logger.warning("Comando desconhecido ignorado: %s", envelope.type)
        return "invalid"
    return handler(get_dynamo(), envelope)
