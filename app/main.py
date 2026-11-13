import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1 import estoque, execucoes, health
from app.config import get_settings
from app.core.logging import setup_json_logging
from app.core.middleware import ObservabilityMiddleware

settings = get_settings()
setup_json_logging(
    service_name=settings.NEW_RELIC_APP_NAME,
    environment=settings.APP_ENV,
    log_level=settings.LOG_LEVEL,
)
logger = logging.getLogger("app.main")

if settings.NEW_RELIC_LICENSE_KEY or os.getenv("NEW_RELIC_LICENSE_KEY"):
    try:  # pragma: no cover - depende do agente
        import newrelic.agent

        os.environ.setdefault("NEW_RELIC_APP_NAME", settings.NEW_RELIC_APP_NAME)
        os.environ.setdefault("NEW_RELIC_DISTRIBUTED_TRACING_ENABLED", "true")
        newrelic.agent.initialize()
        logger.info("Agente APM New Relic inicializado.")
    except Exception as exc:  # pragma: no cover
        logger.warning("Não foi possível inicializar o New Relic: %s", exc)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.DYNAMODB_CREATE_TABLES:
        from app.infrastructure.dynamo import get_dynamo

        get_dynamo().ensure_tables()
    workers = None
    if settings.RUN_WORKERS:
        from app.workers import BackgroundWorkers

        workers = BackgroundWorkers(settings)
        workers.start()
        logger.info("Workers do Execution iniciados no processo da API")
    yield
    if workers is not None:
        workers.stop()


app = FastAPI(
    title="Oficina Mecânica - Execution Service",
    description=(
        "Estoque de peças, fila de execução, diagnóstico e reparo (**DynamoDB**). "
        "Participante da saga: reserva de peças com `TransactWriteItems` "
        "condicional e liberação como compensação."
    ),
    version="1.0.0",
    docs_url="/execution/docs",
    redoc_url="/execution/redoc",
    openapi_url="/execution/openapi.json",
    lifespan=lifespan,
)
app.add_middleware(ObservabilityMiddleware)

app.include_router(health.router)
app.include_router(execucoes.router, prefix="/api/v1")
app.include_router(estoque.router, prefix="/api/v1")


@app.get("/", include_in_schema=False)
def root():
    return {"status": "ok", "service": settings.SERVICE_NAME}
