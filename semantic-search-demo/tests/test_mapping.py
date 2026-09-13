import base64
import struct
from dataclasses import replace

import pytest
from opensearchpy import NotFoundError
from opensearchpy.helpers.errors import BulkIndexError

from semantic_search.config import config
from semantic_search.opensearch_store import (
    CATALOG_VECTOR_DIMENSION,
    CATALOG_VECTOR_ENGINE,
    CATALOG_VECTOR_FIELD,
    CATALOG_VECTOR_SPACE_TYPE,
    OpenSearchStore,
    catalog_index_definition,
    occurrence_index_definition,
)


def test_occurrence_mapping_has_original_fields_but_no_vector():
    definition = occurrence_index_definition(config)
    properties = definition["mappings"]["properties"]

    assert definition["mappings"]["dynamic"] == "strict"
    assert properties["sentenceEntityId"]["type"] == "long"
    assert properties["applicationId"]["type"] == "keyword"
    assert properties["semanticKey"]["type"] == "keyword"
    assert properties["entitySearchText"]["type"] == "text"
    assert properties["normalizedText"]["normalizer"] == "lowercase_ascii"
    assert properties["possibleSanction"]["type"] == "boolean"
    assert properties["score"]["type"] == "float"
    assert all(
        property_definition.get("type") != "semantic"
        for property_definition in properties.values()
    )


def test_catalog_mapping_uses_explicit_cosine_vector_and_ingest_pipeline():
    definition = catalog_index_definition(
        replace(
            config,
            semantic_model_id="opensearch-model-123",
            ingest_pipeline="titan-pipeline",
        )
    )
    properties = definition["mappings"]["properties"]

    assert definition["settings"]["index.knn"] is True
    assert "index.knn.space_type" not in definition["settings"]
    assert definition["settings"]["index.default_pipeline"] == "titan-pipeline"
    assert definition["settings"]["number_of_shards"] == 1
    assert definition["settings"]["number_of_replicas"] == 1
    assert set(properties) == {
        "semanticKey",
        "normalizedText",
        "entitySearchText",
        "entitySearchTextVector",
    }
    assert properties["entitySearchText"] == {"type": "text"}
    assert properties["entitySearchTextVector"] == {
        "type": "knn_vector",
        "dimension": 1024,
        "method": {
            "name": "hnsw",
            "space_type": "cosinesimil",
            "engine": "faiss",
        },
    }


def test_catalog_mapping_requires_an_opensearch_model_id():
    with pytest.raises(ValueError, match="OPENSEARCH_SEMANTIC_MODEL_ID"):
        catalog_index_definition(replace(config, semantic_model_id=""))


def test_catalog_mapping_requires_an_ingest_pipeline():
    with pytest.raises(ValueError, match="OPENSEARCH_INGEST_PIPELINE"):
        catalog_index_definition(
            replace(
                config,
                semantic_model_id="opensearch-model-123",
                ingest_pipeline="",
            )
        )


def _catalog_mapping(
    dimension=CATALOG_VECTOR_DIMENSION,
    space_type=CATALOG_VECTOR_SPACE_TYPE,
    engine=CATALOG_VECTOR_ENGINE,
):
    return {
        config.catalog_index: {
            "mappings": {
                "properties": {
                    "entitySearchText": {"type": "text"},
                    CATALOG_VECTOR_FIELD: {
                        "type": "knn_vector",
                        "dimension": dimension,
                        "method": {
                            "name": "hnsw",
                            "space_type": space_type,
                            "engine": engine,
                        },
                    },
                }
            }
        }
    }


def _catalog_settings(pipeline="titan-pipeline"):
    return {
        config.catalog_index: {
            "settings": {
                "index.default_pipeline": pipeline,
            }
        }
    }


def test_store_accepts_pipeline_cosine_catalog_mapping():
    class FakeIndices:
        @staticmethod
        def get_mapping(index):
            assert index == config.catalog_index
            return _catalog_mapping()

        @staticmethod
        def get_settings(index, params):
            assert index == config.catalog_index
            assert params == {"flat_settings": "true"}
            return _catalog_settings()

    class FakeClient:
        indices = FakeIndices()

    store = OpenSearchStore(
        replace(
            config,
            semantic_model_id="opensearch-model-123",
            ingest_pipeline="titan-pipeline",
        ),
        client=FakeClient(),
    )

    store._validate_catalog_mapping()

    assert store.vector_space_type == "cosinesimil"


def test_store_rejects_non_cosine_catalog_mapping():
    class FakeIndices:
        @staticmethod
        def get_mapping(index):
            assert index == config.catalog_index
            return _catalog_mapping(space_type="l2")

        @staticmethod
        def get_settings(index, params):
            assert index == config.catalog_index
            assert params == {"flat_settings": "true"}
            return _catalog_settings()

    class FakeClient:
        indices = FakeIndices()

    store = OpenSearchStore(
        replace(
            config,
            semantic_model_id="opensearch-model-123",
            ingest_pipeline="titan-pipeline",
        ),
        client=FakeClient(),
    )

    with pytest.raises(RuntimeError, match="must be cosinesimil"):
        store._validate_catalog_mapping()


def test_store_rejects_non_faiss_catalog_mapping():
    class FakeIndices:
        @staticmethod
        def get_mapping(index):
            assert index == config.catalog_index
            return _catalog_mapping(engine="lucene")

        @staticmethod
        def get_settings(index, params):
            assert index == config.catalog_index
            assert params == {"flat_settings": "true"}
            return _catalog_settings()

    class FakeClient:
        indices = FakeIndices()

    store = OpenSearchStore(
        replace(
            config,
            semantic_model_id="opensearch-model-123",
            ingest_pipeline="titan-pipeline",
        ),
        client=FakeClient(),
    )

    with pytest.raises(RuntimeError, match="engine must be faiss"):
        store._validate_catalog_mapping()


def test_store_rejects_wrong_catalog_vector_dimension():
    class FakeIndices:
        @staticmethod
        def get_mapping(index):
            assert index == config.catalog_index
            return _catalog_mapping(dimension=1536)

    class FakeClient:
        indices = FakeIndices()

    store = OpenSearchStore(config, client=FakeClient())

    with pytest.raises(RuntimeError, match="expected 1024"):
        store._validate_catalog_mapping()


def test_store_stats_counts_both_indexes():
    class FakeCluster:
        @staticmethod
        def health():
            return {"status": "green"}

    class FakeClient:
        cluster = FakeCluster()

        @staticmethod
        def count(index):
            counts = {
                config.occurrence_alias: 26,
                config.catalog_alias: 4,
            }
            return {"count": counts[index]}

    store = OpenSearchStore(config, client=FakeClient())

    assert store.stats() == {
        "occurrenceDocuments": 26,
        "semanticCatalogDocuments": 4,
        "clusterHealth": "green",
    }


def test_bulk_skips_per_batch_refresh_and_refreshes_both_indexes_once(
    monkeypatch,
):
    bulk_arguments = {}

    def fake_bulk(client, actions, **kwargs):
        bulk_arguments.update(
            client=client,
            actions=actions,
            **kwargs,
        )

    class FakeIndices:
        def __init__(self):
            self.refreshed_index = None

        def refresh(self, index):
            self.refreshed_index = index

    class FakeClient:
        indices = FakeIndices()

    monkeypatch.setattr(
        "semantic_search.opensearch_store.helpers.bulk",
        fake_bulk,
    )
    client = FakeClient()
    store = OpenSearchStore(config, client=client)
    actions = [{"_index": config.catalog_alias, "_source": {"value": 1}}]

    store._bulk(actions)
    store.refresh_indices()

    assert bulk_arguments == {
        "client": client,
        "actions": actions,
        "chunk_size": config.seed_batch_size,
        "request_timeout": 120,
    }
    assert client.indices.refreshed_index == (
        f"{config.occurrence_alias},{config.catalog_alias}"
    )


def test_catalog_bulk_retries_only_failed_500_documents(monkeypatch):
    store = OpenSearchStore(config, client=object())
    attempted_ids = []
    delays = []
    clock = [0.0]

    def fake_bulk(actions):
        attempted_ids.append([action["_id"] for action in actions])
        if len(attempted_ids) == 1:
            raise BulkIndexError(
                "1 document failed",
                [
                    {
                        "index": {
                            "_id": "key-2",
                            "status": 500,
                            "error": {"type": "status_exception"},
                        }
                    }
                ],
            )

    monkeypatch.setattr(store, "_bulk", fake_bulk)

    def fake_sleep(seconds):
        delays.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr("semantic_search.opensearch_store.time.sleep", fake_sleep)
    monkeypatch.setattr(
        "semantic_search.opensearch_store.time.monotonic",
        lambda: clock[0],
    )

    store.bulk_index_catalog(
        [
            {"semanticKey": "key-1", "entitySearchText": "one"},
            {"semanticKey": "key-2", "entitySearchText": "two"},
        ]
    )

    assert attempted_ids == [["key-1", "key-2"], ["key-2"]]
    assert delays == [5]


def test_catalog_bulk_stops_after_ten_transient_failures(monkeypatch):
    store = OpenSearchStore(config, client=object())
    attempts = 0
    delays = []
    clock = [0.0]

    def always_fail(_):
        nonlocal attempts
        attempts += 1
        raise BulkIndexError(
            "1 document failed",
            [{"index": {"_id": "key-1", "status": 500}}],
        )

    monkeypatch.setattr(store, "_bulk", always_fail)

    def fake_sleep(seconds):
        delays.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr("semantic_search.opensearch_store.time.sleep", fake_sleep)
    monkeypatch.setattr(
        "semantic_search.opensearch_store.time.monotonic",
        lambda: clock[0],
    )

    with pytest.raises(BulkIndexError):
        store.bulk_index_catalog([{"semanticKey": "key-1", "entitySearchText": "one"}])

    assert attempts == 10
    assert delays == [5] * 9


def test_catalog_bulk_does_not_retry_permanent_400_errors(monkeypatch):
    store = OpenSearchStore(config, client=object())
    attempts = 0
    delays = []

    def fail_with_mapping_error(_):
        nonlocal attempts
        attempts += 1
        raise BulkIndexError(
            "1 document failed",
            [{"index": {"_id": "key-1", "status": 400}}],
        )

    monkeypatch.setattr(store, "_bulk", fail_with_mapping_error)
    monkeypatch.setattr(
        "semantic_search.opensearch_store.time.sleep",
        delays.append,
    )

    with pytest.raises(BulkIndexError):
        store.bulk_index_catalog([{"semanticKey": "key-1", "entitySearchText": "one"}])

    assert attempts == 1
    assert delays == []


def test_existing_catalog_keys_uses_batched_mget_without_sources(monkeypatch):
    class FakeClient:
        @staticmethod
        def mget(index, body, params):
            assert index == config.catalog_alias
            assert params == {"_source": "false"}
            return {
                "docs": [
                    {"_id": key, "found": key.endswith("1")}
                    for key in body["ids"]
                ]
            }

    monkeypatch.setattr(
        "semantic_search.opensearch_store.CATALOG_EXISTENCE_BATCH_SIZE",
        2,
    )
    store = OpenSearchStore(config, client=FakeClient())

    assert store.existing_catalog_keys(["key-1", "key-2", "key-3"]) == {
        "key-1"
    }


def test_catalog_vectors_use_binary_doc_values_without_source():
    vector = [0.1, 0.2] + [0.0] * (CATALOG_VECTOR_DIMENSION - 2)
    encoded_vector = base64.b64encode(
        struct.pack(f"<{CATALOG_VECTOR_DIMENSION}f", *vector)
    ).decode("ascii")

    class FakeClient:
        @staticmethod
        def search(index, body):
            assert index == config.catalog_alias
            assert body == {
                "size": 1,
                "track_total_hits": False,
                "_source": False,
                "stored_fields": "_none_",
                "docvalue_fields": [
                    "semanticKey",
                    {
                        "field": "entitySearchTextVector",
                        "format": "binary",
                    }
                ],
                "query": {"ids": {"values": ["key-1"]}},
            }
            return {
                "hits": {
                    "hits": [
                        {
                            "fields": {
                                "semanticKey": ["key-1"],
                                "entitySearchTextVector": [encoded_vector]
                            },
                        }
                    ]
                }
            }

    store = OpenSearchStore(config, client=FakeClient())
    result = store.catalog_vectors(["key-1"])["key-1"]

    assert len(result) == CATALOG_VECTOR_DIMENSION
    assert result[:2] == pytest.approx([0.1, 0.2])


def test_store_previews_ten_unfiltered_documents_with_total_count():
    class FakeClient:
        @staticmethod
        def search(index, body):
            assert index == config.catalog_alias
            assert body == {
                "size": 10,
                "track_total_hits": True,
                "query": {"match_all": {}},
            }
            return {
                "hits": {
                    "total": {"value": 16, "relation": "eq"},
                    "hits": [
                        {
                            "_index": config.catalog_index,
                            "_id": str(number),
                            "_source": {"entitySearchText": f"Entity {number}"},
                        }
                        for number in range(10)
                    ],
                }
            }

    store = OpenSearchStore(config, client=FakeClient())

    result = store.preview_documents(config.catalog_alias)

    assert result["totalDocuments"] == 16
    assert result["returnedDocuments"] == 10
    assert len(result["documents"]) == 10


def test_store_deletes_both_exact_aliases_and_indices():
    class FakeIndices:
        def __init__(self):
            self.indices = {
                config.occurrence_index,
                config.catalog_index,
            }
            self.alias_targets = {
                config.occurrence_alias: {config.occurrence_index},
                config.catalog_alias: {config.catalog_index},
            }
            self.deleted_indices = []
            self.deleted_alias_bindings = []

        def exists_alias(self, name):
            return bool(self.alias_targets.get(name))

        def get_alias(self, name):
            return {
                index: {"aliases": {name: {}}} for index in self.alias_targets[name]
            }

        def delete_alias(self, index, name):
            self.deleted_alias_bindings.append((index, name))
            self.alias_targets[name].remove(index)

        def exists(self, index):
            return index in self.indices

        def delete(self, index):
            self.deleted_indices.append(index)
            self.indices.remove(index)

    class FakeClient:
        indices = FakeIndices()

    client = FakeClient()
    store = OpenSearchStore(config, client=client)
    store.vector_space_type = "cosinesimil"

    result = store.delete_indices_and_aliases()

    assert result == {
        "deletedIndices": [
            config.occurrence_index,
            config.catalog_index,
        ],
        "missingIndices": [],
        "deletedAliases": [
            config.occurrence_alias,
            config.catalog_alias,
        ],
        "missingAliases": [],
    }
    assert client.indices.deleted_alias_bindings == [
        (config.occurrence_index, config.occurrence_alias),
        (config.catalog_index, config.catalog_alias),
    ]
    assert client.indices.deleted_indices == [
        config.occurrence_index,
        config.catalog_index,
    ]
    assert store.vector_space_type is None


def test_store_stats_reports_zero_when_indexes_are_deleted():
    class FakeCluster:
        @staticmethod
        def health():
            return {"status": "green"}

    class FakeClient:
        cluster = FakeCluster()

        @staticmethod
        def count(index):
            raise NotFoundError(404, "index_not_found_exception")

    store = OpenSearchStore(config, client=FakeClient())

    assert store.stats() == {
        "occurrenceDocuments": 0,
        "semanticCatalogDocuments": 0,
        "clusterHealth": "green",
    }
