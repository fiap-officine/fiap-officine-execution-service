"""Processo standalone dos workers do Execution (consumer e outbox relay).
Alternativa a RUN_WORKERS=true.

Uso: python -m app.worker
"""

import logging
import signal

from app.config import get_settings
from app.core.logging import setup_json_logging
from app.workers import BackgroundWorkers

logger = logging.getLogger("worker")


def main() -> None:  # pragma: no cover - entrypoint de processo
    settings = get_settings()
    setup_json_logging(
        service_name=f"{settings.NEW_RELIC_APP_NAME}-worker",
        environment=settings.APP_ENV,
        log_level=settings.LOG_LEVEL,
    )
    workers = BackgroundWorkers(settings)

    def handle_signal(signum, frame):
        logger.info("Sinal %s recebido. Encerrando workers...", signum)
        workers.stop()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    workers.start()
    workers.wait()


if __name__ == "__main__":  # pragma: no cover
    main()
