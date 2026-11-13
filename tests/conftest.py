import os

os.environ["NEW_RELIC_LICENSE_KEY"] = ""
os.environ["RUN_WORKERS"] = "false"
os.environ["DYNAMODB_CREATE_TABLES"] = "false"
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_SESSION_TOKEN"] = "testing"
os.environ["AWS_DEFAULT_REGION"] = "sa-east-1"
os.environ["AWS_REGION"] = "sa-east-1"
os.environ.pop("AWS_ENDPOINT_URL", None)
os.environ["SQS_WAIT_TIME_SECONDS"] = "0"
os.environ["JWT_SECRET_KEY"] = "segredo-jwt-teste"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from jose import jwt  # noqa: E402
from moto import mock_aws  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.infrastructure.dynamo import Dynamo, set_dynamo  # noqa: E402
from app.messaging.sqs import SqsBus, set_bus  # noqa: E402

QUEUES = ("execution-commands", "os-saga-replies")


def drain_queue(bus: SqsBus, logical: str) -> list[dict]:
    drained = []
    while True:
        msgs = bus.receive(logical, wait_seconds=0, max_messages=10)
        if not msgs:
            return drained
        for msg in msgs:
            bus.delete(logical, msg["ReceiptHandle"])
        drained.extend(msgs)


@pytest.fixture(scope="session")
def _aws_session():
    settings = get_settings()
    with mock_aws():
        bus = SqsBus(settings)
        for logical in QUEUES:
            bus.client.create_queue(QueueName=settings.queue_name(logical))
        yield bus, Dynamo(settings)


@pytest.fixture(autouse=True)
def sqs_bus(_aws_session):
    bus, _ = _aws_session
    for logical in QUEUES:
        drain_queue(bus, logical)
    set_bus(bus)
    yield bus
    set_bus(None)


@pytest.fixture(autouse=True)
def dynamo(_aws_session):
    _, dyn = _aws_session
    for table in (dyn.execucoes, dyn.estoque):
        if table in dyn.client.list_tables()["TableNames"]:
            dyn.client.delete_table(TableName=table)
    dyn.ensure_tables()
    set_dynamo(dyn)
    yield dyn
    set_dynamo(None)


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def make_token(role: str = "admin", sub: str = "mecanico.ze") -> str:
    return jwt.encode(
        {"sub": sub, "role": role}, "segredo-jwt-teste", algorithm="HS256"
    )


@pytest.fixture
def headers():
    return {"Authorization": f"Bearer {make_token()}"}
