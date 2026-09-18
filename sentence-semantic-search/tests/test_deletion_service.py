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
        return 2


class FakePostgresStore:
    def delete_sentences(self, application_id, tsp_id=None):
        return {"sentencesDeleted": 4, "matchListsUpdated": 7}


def test_tsp_deletion_removes_both_stores_and_unused_catalog_records():
    from sentence_search.deletion_service import SentenceDeletionService

    opensearch = FakeOpenSearchStore()
    service = SentenceDeletionService(opensearch, FakePostgresStore())

    result = service.delete_tsp("TSP-1", "A1")

    assert opensearch.deleted_scope == ("A1", "TSP-1")
    assert opensearch.catalog_keys == ["key-1", "key-2"]
    assert result["occurrencesDeleted"] == 4
    assert result["catalogDocumentsDeleted"] == 2
    assert result["sentencesDeleted"] == 4
    assert result["matchListsUpdated"] == 7
