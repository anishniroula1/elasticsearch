import base64
import json
import struct

import pytest
from opensearchpy.helpers.errors import BulkIndexError

from sentence_search.config import config
from sentence_search.opensearch_store import (
    OpenSearchStore,
    catalog_index_definition,
    occurrence_index_definition,
)


class DeleteByQueryClient:
    def __init__(self):
        self.request = None

    def delete_by_query(self, **request):
        self.request = request
        return {"deleted": 3}


class EmptySearchClient:
    def __init__(self):
        self.request = None

    def search(self, **request):
        self.request = request
        return {"hits": {"total": {"value": 0}, "hits": []}}


class ClientThatMustNotRun:
    def mget(self, **request):
        raise AssertionError("Duplicate input should fail before OpenSearch")


class ExistingOccurrenceClient:
    def mget(self, **request):
        assert request["body"]["ids"] == ["1"]
        return {
            "docs": [
                {
                    "_id": "1",
                    "found": True,
                    "_source": {
                        "applicationId": "A1",
                        "tspId": "T1",
                        "sectionName": "Affidavit",
                        "analysisGroup": "Asylee",
                        "sentenceKey": "key-1",
                        "isTracer": False,
                        "isFormLanguage": False,
                    },
                }
            ]
        }


class SummaryAggregationClient:
    def __init__(self):
        self.requests = []

    def search(self, **request):
        self.requests.append(request)
        return {
            "aggregations": {
                "keySections": {
                    "buckets": [
                        {
                            "key": {
                                "sectionName": "Affidavit",
                                "sentenceKey": "key-1",
                            },
                            "doc_count": 3,
                        }
                    ]
                }
            }
        }


class CatalogPreviewClient:
    def __init__(self):
        self.request = None

    def search(self, **request):
        self.request = request
        vector_bytes = struct.pack("<512f", *([0.25] * 512))
        return {
            "hits": {
                "hits": [
                    {
                        "_id": "key-1",
                        "_source": {
                            "sentenceKey": "key-1",
                            "sentenceContent": "A sentence",
                        },
                        "fields": {
                            "sentenceContentVector": [
                                base64.b64encode(vector_bytes).decode("ascii")
                            ]
                        },
                    }
                ]
            }
        }


class CatalogVectorsWithoutDocumentIdClient:
    def __init__(self):
        self.request = None

    def search(self, **request):
        self.request = request
        vector_bytes = struct.pack("<512f", *([0.5] * 512))
        return {
            "hits": {
                "hits": [
                    {
                        "fields": {
                            "sentenceKey": ["key-1"],
                            "sentenceContentVector": [
                                base64.b64encode(vector_bytes).decode("ascii")
                            ],
                        }
                    }
                ]
            }
        }


def test_occurrence_mapping_has_requested_sentence_fields():
    properties = occurrence_index_definition(config)["mappings"]["properties"]
    assert set(properties) == {
        "applicationId",
        "tspId",
        "sectionName",
        "globalId",
        "sentIdLocal",
        "sentenceContent",
        "isTracer",
        "isFormLanguage",
        "sentenceKey",
        "sourceType",
        "createdAt",
        "updatedAt",
        "analysisGroup",
    }
    assert properties["globalId"] == {"type": "long"}


def test_catalog_mapping_uses_512_dimension_faiss_cosine():
    definition = catalog_index_definition(config)
    vector = definition["mappings"]["properties"]["sentenceContentVector"]
    assert vector["dimension"] == 512
    assert vector["method"]["engine"] == "faiss"
    assert vector["method"]["space_type"] == "cosinesimil"
    assert "parameters" not in vector["method"]


def test_catalog_retry_keeps_only_temporary_failed_documents():
    store = OpenSearchStore(config, EmptySearchClient())
    actions = [
        {"_id": "key-1", "_source": {"sentenceKey": "key-1"}},
        {"_id": "key-2", "_source": {"sentenceKey": "key-2"}},
    ]
    error = BulkIndexError(
        "one temporary failure",
        [{"index": {"_id": "key-2", "status": 500}}],
    )

    retry_actions = store._retryable_catalog_actions(error, actions)

    assert retry_actions == [actions[1]]


def test_catalog_preview_returns_the_decoded_embedding_list():
    client = CatalogPreviewClient()
    store = OpenSearchStore(config, client)

    result = store.preview(config.catalog_alias, 10)

    document = result["documents"][0]
    assert client.request["body"]["docvalue_fields"] == [
        {"field": "sentenceContentVector", "format": "binary"}
    ]
    assert len(document["sentenceContentVector"]) == 512
    assert document["sentenceContentVector"][0] == pytest.approx(0.25)


def test_catalog_vectors_use_sentence_key_when_search_hit_has_no_id():
    client = CatalogVectorsWithoutDocumentIdClient()
    store = OpenSearchStore(config, client)

    vectors = store.catalog_vectors(["key-1"])

    assert client.request["body"]["docvalue_fields"] == [
        "sentenceKey",
        {"field": "sentenceContentVector", "format": "binary"},
    ]
    assert len(vectors["key-1"]) == 512
    assert vectors["key-1"][0] == pytest.approx(0.5)


def test_tsp_deletion_uses_camel_case_opensearch_fields():
    client = DeleteByQueryClient()
    store = OpenSearchStore(config, client)

    deleted = store.delete_occurrences("A1", "TSP-1")

    filters = client.request["body"]["query"]["bool"]["filter"]
    assert filters == [
        {"term": {"applicationId": "A1"}},
        {"term": {"tspId": "TSP-1"}},
    ]
    assert deleted == 3


def test_application_page_excludes_tracer_and_form_language():
    client = EmptySearchClient()
    store = OpenSearchStore(config, client)

    result = store.application_occurrence_page(
        "A1",
        "Asylee",
        100,
        None,
        True,
    )

    filters = client.request["body"]["query"]["bool"]["filter"]
    assert result["sentences"] == []
    assert {"term": {"applicationId": "A1"}} in filters
    assert {"term": {"analysisGroup": "Asylee"}} in filters
    assert {"term": {"isTracer": False}} in filters
    assert {"term": {"isFormLanguage": False}} in filters


def test_sentence_key_search_uses_terms_and_never_knn():
    client = EmptySearchClient()
    store = OpenSearchStore(config, client)

    store.matching_occurrence_page(
        ["key-1", "key-2"],
        "A1",
        "Asylee",
        100,
        None,
        False,
    )

    body = client.request["body"]
    query_text = json.dumps(body["query"])
    assert '"terms": {"sentenceKey": ["key-1", "key-2"]}' in query_text
    assert '"knn"' not in query_text
    assert {"term": {"applicationId": "A1"}} in body["query"]["bool"]["must_not"]


def test_summary_refresh_groups_source_counts_by_section_and_key():
    client = SummaryAggregationClient()
    store = OpenSearchStore(config, client)

    counts = store.application_key_section_counts("A1", "Asylee")

    assert counts == [
        {
            "sectionName": "Affidavit",
            "sentenceKey": "key-1",
            "occurrenceCount": 3,
        }
    ]


def test_duplicate_global_id_cannot_have_different_sentence_keys():
    store = OpenSearchStore(config, ClientThatMustNotRun())
    first = _occurrence_identity("key-1")
    second = _occurrence_identity("key-2")

    with pytest.raises(ValueError, match="different sentence data"):
        store.validate_occurrence_identity([first, second])


def test_numeric_global_id_uses_string_document_id_for_mget():
    store = OpenSearchStore(config, ExistingOccurrenceClient())

    store.validate_occurrence_identity([_occurrence_identity("key-1")])


def _occurrence_identity(sentence_key):
    return {
        "globalId": 1,
        "applicationId": "A1",
        "tspId": "T1",
        "sectionName": "Affidavit",
        "analysisGroup": "Asylee",
        "sentenceKey": sentence_key,
        "isTracer": False,
        "isFormLanguage": False,
    }
