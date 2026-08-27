import pytest
from botocore.credentials import Credentials
from opensearchpy.exceptions import NotFoundError
from pydantic import ValidationError

import ner_sync.entity_handler as entity_handler
import ner_sync.open_search_client as open_search_client
from ner_sync.config import Config


VALID_ENVIRONMENT = {
    "OPENSEARCH_HOST": "search-demo.us-east-1.es.amazonaws.com",
    "AWS_REGION": "us-east-1",
    "OPENSEARCH_INDEX": "entities",
    "OPENSEARCH_SERVICE": "es",
    "IAM_ACCESS_ROLE": "arn:aws:iam::123456789012:role/search-access",
}


class FakeOpenSearch:
    def __init__(self):
        self.index_call = {}
        self.delete_call = {}
        self.index_error = False
        self.delete_error = False
        self.index_result = {"result": "created"}
        self.settings = {}

    def index(self, **kwargs):
        if self.index_error:
            raise RuntimeError("OpenSearch is unavailable")
        self.index_call = kwargs
        return self.index_result

    def delete(self, **kwargs):
        if self.delete_error:
            raise NotFoundError(404, "Document is already gone", {})
        self.delete_call = kwargs
        return {"result": "deleted"}


class FakeStsClient:
    def __init__(self):
        self.assume_role_call = {}

    def assume_role(self, **kwargs):
        self.assume_role_call = kwargs
        return {
            "Credentials": {
                "AccessKeyId": "temporary-access-key",
                "SecretAccessKey": "temporary-secret-key",
                "SessionToken": "temporary-session-token",
            }
        }


class FakeSession:
    def __init__(self, **kwargs):
        self.credentials = Credentials(
            kwargs["aws_access_key_id"],
            kwargs["aws_secret_access_key"],
            kwargs["aws_session_token"],
        )

    def get_credentials(self):
        return self.credentials


def create_event(event_type="SENTENCE_ENTITY_CREATED"):
    return {
        "eventType": event_type,
        "sentenceEntityId": 10,
        "applicationId": "A1",
        "tspId": "T1",
        "globalId": "G1",
        "entityId": "E1",
        "rawEntity": "Jack X",
        "normalizedText": "jack x",
        "entityType": "PERSON",
        "possibleSanction": False,
        "beginOffset": 4,
        "endOffset": 10,
        "score": 0.98,
    }


def delete_event():
    return {
        "eventType": "SENTENCE_ENTITY_DELETED",
        "sentenceEntityId": 10,
    }


@pytest.fixture(autouse=True)
def aws_environment(monkeypatch):
    """Use fake AWS credentials and fresh settings for every test."""

    sts_client = FakeStsClient()
    for name, value in VALID_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        open_search_client.boto3,
        "client",
        lambda service, region_name: sts_client,
    )
    monkeypatch.setattr(
        open_search_client.boto3,
        "Session",
        lambda **kwargs: FakeSession(**kwargs),
    )
    Config.get_config.cache_clear()
    yield sts_client
    Config.get_config.cache_clear()


@pytest.fixture
def client(monkeypatch):
    fake_client = FakeOpenSearch()

    def create_client(**kwargs):
        fake_client.settings = kwargs
        return fake_client

    monkeypatch.setattr(open_search_client, "OpenSearch", create_client)
    return fake_client


def test_lambda_create_happy_path(client, aws_environment):
    breakpoint()
    result = entity_handler.lambda_handler(create_event(), None)
    document = client.index_call["body"]

    assert result == {
        "action": "created",
        "eventType": "SENTENCE_ENTITY_CREATED",
        "sentenceEntityId": 10,
        "requestId": None,
    }
    breakpoint()
    assert client.index_call["index"] == "entities"
    assert client.index_call["id"] == "10"
    assert document["applicationId"] == "A1"
    assert document["entitySearchText"] == "jack x"
    assert document["createdAt"] == document["updatedAt"]
    assert client.settings["hosts"] == [
        {"host": VALID_ENVIRONMENT["OPENSEARCH_HOST"], "port": 443}
    ]
    assert client.settings["http_auth"]
    assert aws_environment.assume_role_call == {
        "RoleArn": VALID_ENVIRONMENT["IAM_ACCESS_ROLE"],
        "RoleSessionName": "nerOpenSearchIngestSession",
    }


def test_lambda_update_uses_same_document_id(client):
    client.index_result = {"result": "updated"}

    result = entity_handler.lambda_handler(
        create_event("SENTENCE_ENTITY_UPDATED"),
        None,
    )

    assert result["action"] == "updated"
    assert client.index_call["id"] == "10"


@pytest.mark.parametrize(
    ("already_gone", "expected_action"),
    [(False, "deleted"), (True, "already_deleted")],
)
def test_lambda_delete(client, already_gone, expected_action):
    client.delete_error = already_gone

    result = entity_handler.lambda_handler(delete_event(), None)

    assert result["action"] == expected_action
    if not already_gone:
        assert client.delete_call == {"index": "entities", "id": "10"}


def test_lambda_logs_and_rethrows_opensearch_errors(client, caplog):
    client.index_error = True

    with pytest.raises(RuntimeError, match="OpenSearch is unavailable"):
        entity_handler.lambda_handler(create_event(), None)

    assert "Entity event failed" in caplog.text
    assert "sentenceEntityId=10" in caplog.text
    assert "requestId=None" in caplog.text


@pytest.mark.parametrize(
    "event_type",
    ["SENTENCE_ENTITY_CREATED", "SENTENCE_ENTITY_UPDATED"],
)
def test_lambda_rejects_missing_required_entity_data(event_type):
    event = create_event(event_type)
    event.pop("applicationId")

    with pytest.raises(ValidationError, match="applicationId"):
        entity_handler.lambda_handler(event, None)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("endOffset", 2, "endOffset"),
        ("entityId", "   ", "entityId"),
        ("score", 2, "less_than_equal"),
        ("beginOffset", -1, "greater_than_equal"),
        ("unknown", "not allowed", "extra_forbidden"),
    ],
)
def test_lambda_rejects_invalid_entity_values(field, value, message):
    event = create_event()
    event[field] = value

    with pytest.raises(ValidationError, match=message):
        entity_handler.lambda_handler(event, None)


def test_lambda_keeps_document_fields_and_removes_event_metadata(client):
    event = create_event()
    event.update(
        {
            "schemaVersion": 1,
            "eventId": "event-123",
            "occurredAt": "2026-08-22T10:00:00Z",
            "entitySearchText": "jack x person",
            "createdAt": "2026-08-22T10:00:00Z",
            "updatedAt": "2026-08-22T10:05:00Z",
        }
    )

    entity_handler.lambda_handler(event, None)
    document = client.index_call["body"]

    assert document["entitySearchText"] == "jack x person"
    assert document["createdAt"].startswith("2026-08-22T10:00:00")
    assert document["updatedAt"].startswith("2026-08-22T10:05:00")
    assert {"schemaVersion", "eventId", "occurredAt"}.isdisjoint(document)


def test_config_removes_protocol_and_keeps_one_copy(monkeypatch):
    monkeypatch.setenv(
        "OPENSEARCH_HOST",
        "https://search-demo.us-east-1.es.amazonaws.com/a-path",
    )
    Config.get_config.cache_clear()

    first = Config.get_config()

    assert first is Config.get_config()
    assert first.host == "search-demo.us-east-1.es.amazonaws.com"


@pytest.mark.parametrize(
    ("setting", "value", "message"),
    [
        ("OPENSEARCH_HOST", "", "OPENSEARCH_HOST is required"),
        ("AWS_REGION", "", "AWS_REGION is required"),
        ("OPENSEARCH_INDEX", "", "OPENSEARCH_INDEX is required"),
        (
            "OPENSEARCH_SERVICE",
            "invalid",
            "OPENSEARCH_SERVICE must be es or aoss",
        ),
        ("IAM_ACCESS_ROLE", "", "IAM_ACCESS_ROLE is required"),
        ("OPENSEARCH_HOST", "https://", "OPENSEARCH_HOST is invalid"),
    ],
)
def test_lambda_rethrows_invalid_configuration(
    monkeypatch,
    setting,
    value,
    message,
):
    monkeypatch.setenv(setting, value)
    Config.get_config.cache_clear()

    with pytest.raises(ValueError, match=message):
        entity_handler.lambda_handler(create_event(), None)
