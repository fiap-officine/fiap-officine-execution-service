from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── Autenticação (JWT do operador/mecânico emitido pelo OS Service) ──
    JWT_SECRET_KEY: str = "default-secret-key-change-in-production-12345"
    JWT_ALGORITHM: str = "HS256"

    # ── Aplicação ────────────────────────────────
    APP_ENV: str = "development"
    SERVICE_NAME: str = "execution-service"

    # ── AWS (DynamoDB + SQS) ─────────────────────
    AWS_REGION: str = "sa-east-1"
    AWS_ENDPOINT_URL: str | None = None  # LocalStack em dev
    DYNAMODB_TABLE_PREFIX: str = "fiap-officine"
    # Cria as tabelas na subida (dev/LocalStack). Em AWS, o Terraform cria.
    DYNAMODB_CREATE_TABLES: bool = False
    MSG_TTL_DAYS: int = 7

    SQS_QUEUE_PREFIX: str = "fiap-officine"
    SQS_WAIT_TIME_SECONDS: int = 20
    SQS_MAX_MESSAGES: int = 10
    RUN_WORKERS: bool = False
    OUTBOX_RELAY_INTERVAL_SECONDS: float = 1.0

    # ── Observabilidade ──────────────────────────
    NEW_RELIC_LICENSE_KEY: str | None = None
    NEW_RELIC_APP_NAME: str = "officine-execution-service"
    LOG_LEVEL: str = "INFO"

    model_config = {"env_file": ".env", "extra": "ignore"}

    def queue_name(self, logical_name: str) -> str:
        if not self.SQS_QUEUE_PREFIX:
            return logical_name
        return f"{self.SQS_QUEUE_PREFIX}-{logical_name}"

    def table_name(self, logical_name: str) -> str:
        if not self.DYNAMODB_TABLE_PREFIX:
            return logical_name
        return f"{self.DYNAMODB_TABLE_PREFIX}-{logical_name}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
