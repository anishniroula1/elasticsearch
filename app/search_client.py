import time
from typing import Any

from app.config import config
from app.open_search_client import OpenSearchClient


client = OpenSearchClient(config).create_client()


INDEX_DEFINITION: dict[str, Any] = {
    "settings": {
        # One shard and no replica keeps this local demo small.
        # Production values should be chosen from real index size and AZ count.
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "analysis": {
            "normalizer": {
                "lowercase_ascii": {
                    "type": "custom",
                    "filter": ["lowercase", "asciifolding"],
                }
            }
        },
    },
    "mappings": {
        "dynamic": "strict",
        "properties": {
            "sentenceEntityId": {"type": "long"},
            "applicationId": {"type": "keyword"},
            "tspId": {"type": "keyword"},
            "globalId": {"type": "keyword"},
            "entityId": {"type": "keyword"},
            "rawEntity": {"type": "text"},
            "normalizedText": {
                "type": "keyword",
                "normalizer": "lowercase_ascii",
            },
            "entitySearchText": {"type": "text"},
            "entityType": {"type": "keyword"},
            "possibleSanction": {"type": "boolean"},
            "beginOffset": {"type": "integer"},
            "endOffset": {"type": "integer"},
            "score": {"type": "float"},
            "source": {"type": "keyword"},
            "documentType": {"type": "keyword"},
            "createdAt": {"type": "date"},
            "updatedAt": {"type": "date"},
        },
    },
}


def wait_until_ready(max_attempts: int = 60, delay_seconds: int = 2):
    """Wait until OpenSearch is ready before starting API."""

    for _ in range(max_attempts):
        try:
            if client.ping():
                return
        except Exception:
            # Connection errors are expected while the container is starting.
            pass
        time.sleep(delay_seconds)

    raise RuntimeError("OpenSearch did not become ready")


def ensure_index():
    """Create the index and alias if they do not exist."""

    if not client.indices.exists(index=config.physical_index):
        index_body = {
            **INDEX_DEFINITION,
            "aliases": {config.index_alias: {"is_write_index": True}},
        }
        client.indices.create(
            index=config.physical_index,
            body=index_body,
        )
        return

    if not client.indices.exists_alias(name=config.index_alias):
        client.indices.update_aliases(
            body={
                "actions": [
                    {
                        "add": {
                            "index": config.physical_index,
                            "alias": config.index_alias,
                            "is_write_index": True,
                        }
                    }
                ]
            }
        )


def search_index(**body):
    """Run a search against the alias used by every API."""

    # OpenSearch expects the query, aggregations and size inside one body.
    return client.search(index=config.index_alias, body=body)


def recreate_index():
    """Delete the current index and create it again."""

    if client.indices.exists(index=config.physical_index):
        client.indices.delete(index=config.physical_index)
    ensure_index()
