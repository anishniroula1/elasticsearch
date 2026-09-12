from dataclasses import replace

import pytest
from opensearchpy import NotFoundError

from semantic_search.config import config
from semantic_search.opensearch_store import (
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


def test_catalog_mapping_embeds_only_unique_semantic_text_documents():
    definition = catalog_index_definition(
        replace(config, semantic_model_id="opensearch-model-123")
    )
    properties = definition["mappings"]["properties"]

    assert definition["settings"]["index.knn"] is True
    assert definition["settings"]["number_of_shards"] == 1
    assert definition["settings"]["number_of_replicas"] == 1
    assert set(properties) == {
        "semanticKey",
        "normalizedText",
        "entitySearchText",
    }
    assert properties["entitySearchText"] == {
        "type": "semantic",
        "model_id": "opensearch-model-123",
    }


def test_catalog_mapping_requires_an_opensearch_model_id():
    with pytest.raises(ValueError, match="OPENSEARCH_SEMANTIC_MODEL_ID"):
        catalog_index_definition(replace(config, semantic_model_id=""))


def _catalog_mapping(space_type):
    return {
        config.catalog_index: {
            "mappings": {
                "properties": {
                    "entitySearchText": {
                        "type": "semantic",
                        "model_id": "opensearch-model-123",
                    },
                    "entitySearchText_semantic_info": {
                        "properties": {
                            "embedding": {
                                "type": "knn_vector",
                                "method": {"space_type": space_type},
                            }
                        }
                    },
                }
            }
        }
    }


def test_store_accepts_normalized_l2_catalog_mapping():
    class FakeIndices:
        @staticmethod
        def get_mapping(index):
            assert index == config.catalog_index
            return _catalog_mapping("l2")

    class FakeClient:
        indices = FakeIndices()

    store = OpenSearchStore(
        replace(config, semantic_model_id="opensearch-model-123"),
        client=FakeClient(),
    )

    store._validate_catalog_mapping()

    assert store.vector_space_type == "l2"


def test_store_rejects_unsupported_catalog_mapping():
    class FakeIndices:
        @staticmethod
        def get_mapping(index):
            assert index == config.catalog_index
            return _catalog_mapping("innerproduct")

    class FakeClient:
        indices = FakeIndices()

    store = OpenSearchStore(
        replace(config, semantic_model_id="opensearch-model-123"),
        client=FakeClient(),
    )

    with pytest.raises(RuntimeError, match="cosinesimil and l2"):
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


def test_catalog_vectors_uses_mget_source_filter_query_parameter():
    class FakeClient:
        @staticmethod
        def mget(index, body, _source_includes):
            assert index == config.catalog_alias
            assert body == {"ids": ["key-1"]}
            assert _source_includes == [
                "semanticKey",
                "entitySearchText_semantic_info.embedding",
            ]
            return {
                "docs": [
                    {
                        "found": True,
                        "_source": {
                            "semanticKey": "key-1",
                            "entitySearchText_semantic_info": {"embedding": [0.1, 0.2]},
                        },
                    }
                ]
            }

    store = OpenSearchStore(config, client=FakeClient())

    assert store.catalog_vectors(["key-1"]) == {"key-1": [0.1, 0.2]}


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
