"""Loops de background: consumer de execution-commands e relay do outbox
(DynamoDB -> SQS os-saga-replies)."""

import logging
import threading
from collections.abc import Callable

from app.config import Settings, get_settings
from app.handlers import execution_command_handler
from app.infrastructure.dynamo import Dynamo, get_dynamo
from app.messaging.consumer import poll_once
from app.messaging.outbox import relay_pending
from app.messaging.sqs import SqsBus, get_bus

logger = logging.getLogger(__name__)

EXECUTION_COMMANDS = "execution-commands"


def run_consumer_once(
    bus: SqsBus | None = None, wait_seconds: int | None = None
) -> int:
    return poll_once(
        bus or get_bus(),
        EXECUTION_COMMANDS,
        execution_command_handler,
        wait_seconds=wait_seconds,
    )


def run_outbox_relay_once(
    dynamo: Dynamo | None = None, bus: SqsBus | None = None
) -> int:
    return relay_pending(dynamo or get_dynamo(), bus or get_bus())


class BackgroundWorkers:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def _loop(self, name: str, func: Callable[[], object], interval: float) -> None:
        logger.info("Worker '%s' iniciado", name)
        while not self._stop.is_set():
            try:
                func()
            except Exception as exc:
                logger.error("Erro no worker '%s': %s", name, exc, exc_info=True)
                self._stop.wait(5)
                continue
            if interval:
                self._stop.wait(interval)

    def start(self) -> None:
        loops = [
            ("execution-consumer", run_consumer_once, 0),
            (
                "outbox-relay",
                run_outbox_relay_once,
                self.settings.OUTBOX_RELAY_INTERVAL_SECONDS,
            ),
        ]
        for name, func, interval in loops:
            thread = threading.Thread(
                target=self._loop, args=(name, func, interval), name=name, daemon=True
            )
            thread.start()
            self._threads.append(thread)

    def stop(self, timeout: float = 25.0) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=timeout)
        self._threads.clear()

    def wait(self) -> None:
        self._stop.wait()
