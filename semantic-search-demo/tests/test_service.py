from types import SimpleNamespace

from semantic_search.models import EntityOccurrence
from semantic_search.opensearch_store import CATALOG_VECTOR_FIELD
from semantic_search.search_utils import (
    UNIQUE_ENTITY_COUNT_PRECISION,
    SemanticSearchUtilities,
    _cosine_percentage,
    _minimum_opensearch_score,
)
from semantic_search.summary_service import SemanticSummaryService
from semantic_search.text import semantic_key
from semantic_search.text_search_service import SemanticTextSearchService


class FakeStore:
    def __init__(
        self,
        occurrence_responses=None,
        catalog_responses=None,
        msearch_responses=None,
        vectors=None,
        single_vector=None,
        vector_space_type="cosinesimil",
    ):
        self.occurrence_responses = list(occurrence_responses or [])
        self.catalog_responses = list(catalog_responses or [])
        self.msearch_responses = list(msearch_responses or [])
        self.vectors = vectors or {}
        self.single_vector = single_vector
        self.vector_space_type = vector_space_type
        self.config = SimpleNamespace(semantic_model_id="model-123")
        self.occurrence_bodies = []
        self.catalog_bodies = []
        self.msearch_bodies = []

    def search_occurrences(self, body):
        self.occurrence_bodies.append(body)
        return self.occurrence_responses.pop(0)

    def search_catalog(self, body):
        self.catalog_bodies.append(body)
        return self.catalog_responses.pop(0)

    def multi_search_catalog(self, bodies):
        self.msearch_bodies.append(bodies)
        return self.msearch_responses.pop(0)

    def catalog_vectors(self, semantic_keys):
        assert set(semantic_keys) == set(self.vectors)
        return self.vectors

    def catalog_vector(self, semantic_key_value):
        return self.single_vector


def _sample_occurrence(
    entity_id,
    text,
    application_id="A2",
    sentence_entity_id=2,
):
    return {
        "sentenceEntityId": sentence_entity_id,
        "applicationId": application_id,
        "tspId": "TSP-2",
        "globalId": f"G-{sentence_entity_id}",
        "entityId": entity_id,
        "semanticKey": semantic_key(text),
        "rawEntity": text.title(),
        "normalizedText": text,
        "entitySearchText": text,
        "entityType": "ORGANIZATION",
        "possibleSanction": False,
        "beginOffset": 0,
        "endOffset": len(text),
        "score": 0.99,
        "source": "test",
        "documentType": "Interview",
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z",
    }


def _catalog_hit(text, score):
    return {
        "_score": score,
        "_source": {
            "semanticKey": semantic_key(text),
            "normalizedText": text,
            "entitySearchText": text,
        },
    }


def _catalog_response(*hits):
    return {
        "aggregations": {
            "matches": {
                "buckets": [
                    {
                        "key": {
                            "semanticKey": hit["_source"]["semanticKey"]
                        },
                        "sample": {"hits": {"hits": [hit]}},
                    }
                    for hit in hits
                ]
            }
        }
    }


def test_source_entity_has_no_precomputed_vector_field():
    occurrence = EntityOccurrence(
        sentenceEntityId=1,
        applicationId="A1",
        tspId="TSP-1",
        globalId="G-1",
        entityId="E1",
        rawEntity="Jack X",
        normalizedText="jack x",
        entitySearchText="jack x",
        entityType="PERSON",
        possibleSanction=False,
        beginOffset=0,
        endOffset=6,
        score=0.99,
        source="test",
        documentType="Interview",
        createdAt="2026-01-01T00:00:00Z",
        updatedAt="2026-01-01T00:00:00Z",
    )

    source = occurrence.model_dump(mode="json")

    assert source["entitySearchText"] == "jack x"
    assert not any("embedding" in key.lower() for key in source)


def test_first_entity_page_uses_fast_cardinality_total():
    store = FakeStore(
        occurrence_responses=[
            {
                "aggregations": {
                    "entities": {"buckets": []},
                    "totalUniqueEntities": {"value": 42},
                }
            }
        ]
    )

    entities, after_key, total = (
        SemanticSearchUtilities(store).application_entity_summary_page(
            "A1",
            90,
            after_key=None,
            size=100,
            include_total=True,
        )
    )

    assert entities == []
    assert after_key is None
    assert total == 42
    assert store.occurrence_bodies[0]["aggs"][
        "totalUniqueEntities"
    ] == {
        "cardinality": {
            "field": "entityId",
            "precision_threshold": UNIQUE_ENTITY_COUNT_PRECISION,
        }
    }


def test_90_percent_cosine_threshold_uses_095_opensearch_score():
    assert _minimum_opensearch_score(90) == 0.95
    assert _cosine_percentage(0.95) == 90.0
    assert _cosine_percentage(0.96) == 92.0


def test_summary_reuses_catalog_vector_then_runs_one_count_aggregation():
    source_text = "acme corporation"
    source_key = semantic_key(source_text)
    similar_text = "acme company"
    application_response = {
        "aggregations": {
            "entities": {
                "buckets": [
                    {
                        "key": {"entityId": "E1"},
                        "doc_count": 2,
                        "sample": {
                            "hits": {
                                "hits": [
                                    {"_source": _sample_occurrence("E1", source_text)}
                                ]
                            }
                        },
                    }
                ]
            }
        }
    }
    count_response = {
        "aggregations": {
            "matches": {
                "buckets": [
                    {
                        "key": {"semanticKey": source_key},
                        "doc_count": 3,
                    },
                    {
                        "key": {"semanticKey": semantic_key(similar_text)},
                        "doc_count": 4,
                    },
                ]
            }
        }
    }
    store = FakeStore(
        occurrence_responses=[application_response, count_response],
        msearch_responses=[
            [
                _catalog_response(
                    _catalog_hit(source_text, 1.7),
                    _catalog_hit(similar_text, 0.96),
                )
            ]
        ],
        vectors={source_key: [0.1, 0.2]},
    )

    result = SemanticSummaryService(store).application_summary("A1", 90)

    assert result["queryEmbeddingSource"] == "semanticCatalog"
    assert result["entities"][0]["exactMatchCount"] == 3
    assert result["entities"][0]["similarMatchCount"] == 4
    assert len(store.occurrence_bodies) == 2
    vector_query = store.msearch_bodies[0][0]["query"]["bool"]["should"][1]
    assert vector_query == {
        "knn": {
            CATALOG_VECTOR_FIELD: {
                "vector": [0.1, 0.2],
                "min_score": 0.95,
            }
        }
    }
    assert "neural" not in str(vector_query)


def test_text_search_calls_titan_once_then_loads_occurrences():
    exact_text = "acme corporation"
    similar_text = "acme company"
    occurrence_response = {
        "aggregations": {
            "matches": {
                "buckets": [
                    {
                        "key": {"sentenceEntityId": 2},
                        "sample": {
                            "hits": {
                                "hits": [
                                    {
                                        "_source": _sample_occurrence(
                                            "E2",
                                            exact_text,
                                            "A2",
                                            2,
                                        )
                                    }
                                ]
                            }
                        },
                    },
                    {
                        "key": {"sentenceEntityId": 3},
                        "sample": {
                            "hits": {
                                "hits": [
                                    {
                                        "_source": _sample_occurrence(
                                            "E3",
                                            similar_text,
                                            "A3",
                                            3,
                                        )
                                    }
                                ]
                            }
                        },
                    },
                ]
            }
        }
    }
    store = FakeStore(
        occurrence_responses=[occurrence_response],
        catalog_responses=[
            _catalog_response(
                _catalog_hit(exact_text, 1.6),
                _catalog_hit(similar_text, 0.96),
            )
        ],
        single_vector=None,
    )

    result = SemanticTextSearchService(store).search_text(
        "A1",
        exact_text,
        90,
    )

    assert result["queryEmbeddingSource"] == "titan"
    assert result["totalMatches"] == 2
    assert result["matches"][0]["matchType"] == "exact"
    assert result["matches"][1]["matchPercentage"] == 92.0
    catalog_query = store.catalog_bodies[0]["query"]["bool"]["should"][1]
    assert catalog_query["neural"][CATALOG_VECTOR_FIELD] == {
        "query_text": exact_text,
        "model_id": "model-123",
        "min_score": 0.95,
    }
    occurrence_query = store.occurrence_bodies[0]["query"]["bool"]
    assert occurrence_query["must_not"] == [
        {"term": {"applicationId": "A1"}}
    ]


def test_text_search_reuses_catalog_vector_when_text_already_exists():
    text = "acme corporation"
    occurrence_response = {
        "aggregations": {
            "matches": {
                "buckets": [
                    {
                        "key": {"sentenceEntityId": 2},
                        "sample": {
                            "hits": {
                                "hits": [
                                    {
                                        "_source": _sample_occurrence(
                                            "E2",
                                            text,
                                            "A2",
                                            2,
                                        )
                                    }
                                ]
                            }
                        },
                    }
                ]
            }
        }
    }
    store = FakeStore(
        occurrence_responses=[occurrence_response],
        catalog_responses=[_catalog_response(_catalog_hit(text, 1.6))],
        single_vector=[0.1, 0.2],
    )

    result = SemanticTextSearchService(store).search_text("A1", text, 90)

    assert result["queryEmbeddingSource"] == "semanticCatalog"
    catalog_query = store.catalog_bodies[0]["query"]["bool"]["should"][1]
    assert catalog_query == {
        "knn": {
            CATALOG_VECTOR_FIELD: {
                "vector": [0.1, 0.2],
                "min_score": 0.95,
            }
        }
    }
