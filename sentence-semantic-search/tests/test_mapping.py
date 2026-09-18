from dataclasses import replace

import pytest

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


class CandidateSearchClient:
    def __init__(self):
        self.request = None

    def search(self, **request):
        self.request = request
        return {"hits": {"hits": []}}


class ClientThatMustNotRun:
    def mget(self, **request):
        raise AssertionError("Duplicate input should fail before OpenSearch")


def test_occurrence_mapping_has_requested_sentence_fields():
    properties = occurrence_index_definition(config)["mappings"]["properties"]
    assert set(properties) == {
        "applicationId",
        "tspId",
        "documentId",
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


def test_catalog_mapping_uses_512_dimension_faiss_cosine():
    definition = catalog_index_definition(config)
    vector = definition["mappings"]["properties"]["sentenceContentVector"]
    assert vector["dimension"] == 512
    assert vector["method"]["engine"] == "faiss"
    assert vector["method"]["space_type"] == "cosinesimil"


def test_tsp_deletion_uses_camel_case_opensearch_fields():
    client = DeleteByQueryClient()
    store = OpenSearchStore(
        replace(config, match_across_applications_only=True),
        client,
    )

    deleted = store.delete_occurrences("A1", "TSP-1")

    filters = client.request["body"]["query"]["bool"]["filter"]
    assert filters == [
        {"term": {"applicationId": "A1"}},
        {"term": {"tspId": "TSP-1"}},
    ]
    assert deleted == 3


def test_occurrence_candidates_exclude_tracer_form_and_other_analysis_groups():
    client = CandidateSearchClient()
    store = OpenSearchStore(
        replace(config, match_across_applications_only=True),
        client,
    )
    source = {
        "globalId": "S1",
        "applicationId": "A1",
        "analysisGroup": "Asylee",
    }

    targets = store.matching_occurrences(
        source,
        [{"sentenceKey": "key-1"}],
    )

    query = client.request["body"]["query"]["bool"]
    assert targets == []
    assert {"term": {"analysisGroup": "Asylee"}} in query["filter"]
    assert {"term": {"isTracer": False}} in query["filter"]
    assert {"term": {"isFormLanguage": False}} in query["filter"]
    assert {"ids": {"values": ["S1"]}} in query["must_not"]
    assert {"term": {"applicationId": "A1"}} in query["must_not"]


def test_duplicate_global_id_cannot_have_different_sentence_keys():
    store = OpenSearchStore(config, ClientThatMustNotRun())
    first = _occurrence_identity("key-1")
    second = _occurrence_identity("key-2")

    with pytest.raises(ValueError, match="different sentence data"):
        store.validate_occurrence_identity([first, second])


def _occurrence_identity(sentence_key):
    return {
        "globalId": "S1",
        "applicationId": "A1",
        "tspId": "T1",
        "sectionName": "Affidavit",
        "analysisGroup": "Asylee",
        "sentenceKey": sentence_key,
        "isTracer": False,
        "isFormLanguage": False,
    }
