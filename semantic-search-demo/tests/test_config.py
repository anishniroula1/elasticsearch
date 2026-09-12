import pytest

from semantic_search.config import Config, _aws_hostname


def test_aws_hostname_accepts_hostname_or_https_url():
    hostname = "search-demo.us-east-1.es.amazonaws.com"

    assert _aws_hostname(hostname) == hostname
    assert _aws_hostname(f"https://{hostname}") == hostname


def test_aws_hostname_rejects_non_https_url():
    with pytest.raises(ValueError, match="HTTPS Amazon OpenSearch hostname"):
        _aws_hostname("http://localhost:9200")


def test_config_loads_connection_index_and_seed_settings(monkeypatch):
    settings = {
        "OPENSEARCH_HOST": "search-test.us-west-2.es.amazonaws.com",
        "OPENSEARCH_PORT": "8443",
        "OPENSEARCH_SERVICE": "es",
        "AWS_REGION": "us-west-2",
        "IAM_ACCESS_ROLE": "arn:aws:iam::123456789012:role/search",
        "OPENSEARCH_SEMANTIC_MODEL_ID": "model-123",
        "OPENSEARCH_INDEX": "occurrences-v2",
        "OPENSEARCH_ALIAS": "occurrences",
        "OPENSEARCH_CATALOG_INDEX": "catalog-v2",
        "OPENSEARCH_CATALOG_ALIAS": "catalog",
        "INDEX_SHARDS": "3",
        "INDEX_REPLICAS": "2",
        "SEED_BATCH_SIZE": "250",
        "SEED_WORKERS": "6",
    }
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    Config.load.cache_clear()

    loaded = Config.load()

    assert loaded.opensearch_port == 8443
    assert loaded.opensearch_service == "es"
    assert loaded.aws_region == "us-west-2"
    assert loaded.iam_access_role == settings["IAM_ACCESS_ROLE"]
    assert loaded.semantic_model_id == "model-123"
    assert loaded.occurrence_index == "occurrences-v2"
    assert loaded.occurrence_alias == "occurrences"
    assert loaded.catalog_index == "catalog-v2"
    assert loaded.catalog_alias == "catalog"
    assert loaded.index_shards == 3
    assert loaded.index_replicas == 2
    assert loaded.seed_batch_size == 250
    assert loaded.seed_workers == 6
    Config.load.cache_clear()
