"""Cliente fino sobre o Amazon SQS (boto3).

As filas são referenciadas por nome lógico (ex.: "billing-commands"); o nome
físico recebe o prefixo do ambiente (`SQS_QUEUE_PREFIX`). Em ambiente local,
`AWS_ENDPOINT_URL` aponta para o LocalStack.
"""

import logging
import threading
from typing import Any

import boto3
from botocore.config import Config

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


class SqsBus:
    def __init__(self, settings: Settings | None = None, client: Any | None = None):
        self.settings = settings or get_settings()
        self._client = client
        self._urls: dict[str, str] = {}
        self._lock = threading.Lock()

    @property
    def client(self):
        if self._client is None:
            with self._lock:
                if self._client is None:
                    self._client = boto3.client(
                        "sqs",
                        region_name=self.settings.AWS_REGION,
                        endpoint_url=self.settings.AWS_ENDPOINT_URL,
                        config=Config(
                            retries={"max_attempts": 3, "mode": "standard"},
                            connect_timeout=5,
                            read_timeout=self.settings.SQS_WAIT_TIME_SECONDS + 10,
                        ),
                    )
        return self._client

    def queue_url(self, logical_name: str) -> str:
        if logical_name not in self._urls:
            name = self.settings.queue_name(logical_name)
            self._urls[logical_name] = self.client.get_queue_url(QueueName=name)[
                "QueueUrl"
            ]
        return self._urls[logical_name]

    def send(self, logical_name: str, body: str, attributes: dict[str, str]) -> str:
        response = self.client.send_message(
            QueueUrl=self.queue_url(logical_name),
            MessageBody=body,
            MessageAttributes={
                key: {"DataType": "String", "StringValue": str(value)}
                for key, value in attributes.items()
                if value is not None and str(value) != ""
            },
        )
        return response["MessageId"]

    def receive(
        self,
        logical_name: str,
        wait_seconds: int | None = None,
        max_messages: int | None = None,
    ) -> list[dict[str, Any]]:
        response = self.client.receive_message(
            QueueUrl=self.queue_url(logical_name),
            MaxNumberOfMessages=max_messages or self.settings.SQS_MAX_MESSAGES,
            WaitTimeSeconds=(
                self.settings.SQS_WAIT_TIME_SECONDS
                if wait_seconds is None
                else wait_seconds
            ),
            MessageAttributeNames=["All"],
            AttributeNames=["ApproximateReceiveCount"],
        )
        return response.get("Messages", [])

    def delete(self, logical_name: str, receipt_handle: str) -> None:
        self.client.delete_message(
            QueueUrl=self.queue_url(logical_name), ReceiptHandle=receipt_handle
        )

    def ping(self, logical_name: str) -> bool:
        self.client.get_queue_attributes(
            QueueUrl=self.queue_url(logical_name),
            AttributeNames=["ApproximateNumberOfMessages"],
        )
        return True


_bus: SqsBus | None = None


def get_bus() -> SqsBus:
    global _bus
    if _bus is None:
        _bus = SqsBus()
    return _bus


def set_bus(bus: SqsBus | None) -> None:
    """Permite substituir o barramento (testes)."""
    global _bus
    _bus = bus
