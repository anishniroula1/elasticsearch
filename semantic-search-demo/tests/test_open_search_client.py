from dataclasses import replace

from semantic_search.config import config
from semantic_search.open_search_client import OpenSearchClient


def test_client_uses_environment_backed_connection_settings(monkeypatch):
    credentials = object()

    class FakeSession:
        @staticmethod
        def get_credentials():
            return credentials

    signer_calls = []

    def fake_signer(actual_credentials, region, service):
        signer_calls.append((actual_credentials, region, service))
        return "signed-auth"

    monkeypatch.setattr(
        "semantic_search.open_search_client.boto3.Session",
        lambda **kwargs: FakeSession(),
    )
    monkeypatch.setattr(
        "semantic_search.open_search_client.AWSV4SignerAuth",
        fake_signer,
    )
    monkeypatch.setattr(
        "semantic_search.open_search_client.OpenSearch",
        lambda **kwargs: kwargs,
    )
    test_config = replace(
        config,
        opensearch_host="search-test.us-west-2.es.amazonaws.com",
        opensearch_port=8443,
        opensearch_service="es",
        aws_region="us-west-2",
        iam_access_role="",
    )

    client_options = OpenSearchClient(test_config).create_client()

    assert client_options["hosts"] == [
        {
            "host": "search-test.us-west-2.es.amazonaws.com",
            "port": 8443,
        }
    ]
    assert client_options["http_auth"] == "signed-auth"
    assert signer_calls == [(credentials, "us-west-2", "es")]
