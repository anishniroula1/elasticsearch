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
    store = OpenSearchStore(config, client)

    deleted = store.delete_occurrences("A1", "TSP-1")

    filters = client.request["body"]["query"]["bool"]["filter"]
    assert filters == [
        {"term": {"applicationId": "A1"}},
        {"term": {"tspId": "TSP-1"}},
    ]
    assert deleted == 3
