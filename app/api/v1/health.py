import time
from datetime import datetime, timezone

from fastapi import APIRouter, Response, status

from app.config import get_settings
from app.infrastructure.dynamo import get_dynamo
from app.messaging.sqs import get_bus

router = APIRouter(tags=["Health"])
START_TIME = time.time()


@router.get("/health", include_in_schema=False)
@router.get("/execution/health", summary="Healthcheck do Execution Service")
def get_health(response: Response):
    settings = get_settings()
    services: dict = {}
    healthy = True
    try:
        get_dynamo().ping()
        services["database"] = {"status": "up", "type": "DynamoDB"}
    except Exception as exc:
        healthy = False
        services["database"] = {"status": "down", "error": str(exc)[:200]}
    try:
        get_bus().ping("execution-commands")
        services["messaging"] = {"status": "up", "type": "SQS"}
    except Exception as exc:
        services["messaging"] = {"status": "down", "error": str(exc)[:200]}
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "healthy" if healthy else "unhealthy",
        "service": settings.SERVICE_NAME,
        "environment": settings.APP_ENV,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "uptime_seconds": round(time.time() - START_TIME, 2),
        "services": services,
    }
