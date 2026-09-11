import pytest

from semantic_search.config import _aws_hostname


def test_aws_hostname_accepts_hostname_or_https_url():
    hostname = "search-demo.us-east-1.es.amazonaws.com"

    assert _aws_hostname(hostname) == hostname
    assert _aws_hostname(f"https://{hostname}") == hostname


def test_aws_hostname_rejects_non_https_url():
    with pytest.raises(ValueError, match="HTTPS Amazon OpenSearch hostname"):
        _aws_hostname("http://localhost:9200")
