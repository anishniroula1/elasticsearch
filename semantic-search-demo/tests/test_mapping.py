from dataclasses import replace

import pytest

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
