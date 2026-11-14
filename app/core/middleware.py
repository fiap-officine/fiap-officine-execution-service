import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import correlation_id_ctx

logger = logging.getLogger("app.observability")


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """
    Middleware de observabilidade que:
    1. Gerencia Correlation ID e Request ID entre cliente, API e serviços dependentes.
    2. Alimenta atributos customizados no APM do New Relic.
    3. Mede a latência da requisição em milissegundos.
    4. Registra logs de auditoria estruturados em JSON para cada chamada.
    5. Injeta headers de rastreamento na resposta HTTP (X-Correlation-ID, X-Request-ID).
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        start_time = time.perf_counter()

        # Extrai ou gera Correlation ID e Request ID
        correlation_id = (
            request.headers.get("x-correlation-id")
            or request.headers.get("x-request-id")
            or str(uuid.uuid4())
        )
        request_id = str(uuid.uuid4())

        # Configura no ContextVar assíncrono para acesso automático aos logs
        token = correlation_id_ctx.set(correlation_id)

        # Injeta atributos no agente New Relic (se ativo)
        try:
            import newrelic.agent

            newrelic.agent.add_custom_attribute("correlation_id", correlation_id)
            newrelic.agent.add_custom_attribute("request_id", request_id)
            newrelic.agent.add_custom_attribute("http_method", request.method)
            newrelic.agent.add_custom_attribute("request_path", request.url.path)
            if request.client:
                newrelic.agent.add_custom_attribute("client_ip", request.client.host)
        except Exception:
            pass

        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception as exc:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            logger.error(
                f"Erro não tratado na requisição {request.method} {request.url.path}: {exc}",
                exc_info=True,
                extra={
                    "path": request.url.path,
                    "method": request.method,
                    "status_code": 500,
                    "duration_ms": duration_ms,
                    "request_id": request_id,
                },
            )
            # Notifica New Relic sobre a exceção
            try:
                import newrelic.agent

                newrelic.agent.record_exception()
            except Exception:
                pass
            correlation_id_ctx.reset(token)
            raise exc

        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

        # Injeta headers de correlação na resposta
        response.headers["X-Correlation-ID"] = correlation_id
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-Ms"] = str(duration_ms)

        # Registra log estruturado da requisição (apenas avisos/erros ou requisições relevantes)
        if not request.url.path.endswith("/health"):
            logger.info(
                f"{request.method} {request.url.path} finalizado com status {status_code} em {duration_ms}ms",
                extra={
                    "path": request.url.path,
                    "method": request.method,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                    "request_id": request_id,
                },
            )

        correlation_id_ctx.reset(token)
        return response
