from sentence_search.deletion_service import SentenceDeletionService


class FakeOpenSearchStore:
    def __init__(self):
        self.deleted_scope = None
        self.catalog_keys = None

    def deletion_catalog_keys(self, application_id, tsp_id=None):
        return ["key-1", "key-2"]

    def delete_occurrences(self, application_id, tsp_id=None):
        self.deleted_scope = (application_id, tsp_id)
        return 4

    def delete_unused_catalog_keys(self, keys):
        self.catalog_keys = keys
        return {"deleted": 2, "sentenceKeys": ["key-1", "key-2"]}


def test_tsp_deletion_removes_occurrences_and_unused_catalog_records():
    opensearch = FakeOpenSearchStore()
    service = SentenceDeletionService(opensearch)

    result = service.delete_tsp("TSP-1", "A1")

    assert opensearch.deleted_scope == ("A1", "TSP-1")
    assert opensearch.catalog_keys == ["key-1", "key-2"]
    assert result == {
        "applicationId": "A1",
        "tspId": "TSP-1",
        "occurrencesDeleted": 4,
        "catalogDocumentsDeleted": 2,
    }


def test_application_deletion_uses_application_scope():
    opensearch = FakeOpenSearchStore()
    service = SentenceDeletionService(opensearch)

    result = service.delete_application("A1")

    assert opensearch.deleted_scope == ("A1", None)
    assert result["applicationId"] == "A1"
    assert result["tspId"] is None
