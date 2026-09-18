class FakeOpenSearchStore:
    def __init__(self):
        self.deleted_scope = None
        self.catalog_keys = None

    def deletion_catalog_keys(self, application_id, tsp_id=None):
        return ["key-1", "key-2"]

    def deletion_application_groups(self, application_id, tsp_id=None):
        return [{"applicationId": "A1", "analysisGroup": "Asylee"}]

    def delete_occurrences(self, application_id, tsp_id=None):
        self.deleted_scope = (application_id, tsp_id)
        return 4

    def delete_unused_catalog_keys(self, keys):
        self.catalog_keys = keys
        return {"deleted": 2, "sentenceKeys": ["key-1", "key-2"]}

    def application_groups_for_keys(self, keys):
        assert set(keys) == {"key-1", "key-2", "key-3"}
        return [{"applicationId": "A2", "analysisGroup": "Asylee"}]


class FakePostgresStore:
    def __init__(self):
        self.deleted_summary_application = None

    def matching_keys(self, sentence_keys):
        return {
            "key-1": {
                "matchingSentenceKeys": ["key-3"],
            },
            "key-2": {
                "matchingSentenceKeys": [],
            },
        }

    def delete_keys(self, sentence_keys):
        assert sentence_keys == ["key-1", "key-2"]
        return {"keyRowsDeleted": 2, "keyListsUpdated": 7}

    def delete_application_summaries(self, application_id):
        self.deleted_summary_application = application_id
        return 1


class FakeSummaryService:
    def refresh_application_groups(self, groups):
        return len(
            {(group["applicationId"], group["analysisGroup"]) for group in groups}
        )


def test_tsp_deletion_removes_both_stores_and_unused_catalog_records():
    from sentence_search.deletion_service import SentenceDeletionService

    opensearch = FakeOpenSearchStore()
    service = SentenceDeletionService(
        opensearch,
        FakePostgresStore(),
        FakeSummaryService(),
    )

    result = service.delete_tsp("TSP-1", "A1")

    assert opensearch.deleted_scope == ("A1", "TSP-1")
    assert opensearch.catalog_keys == ["key-1", "key-2"]
    assert result["occurrencesDeleted"] == 4
    assert result["catalogDocumentsDeleted"] == 2
    assert result["keyRowsDeleted"] == 2
    assert result["keyListsUpdated"] == 7
    assert result["applicationSummariesRefreshed"] == 2


def test_application_deletion_removes_its_saved_summary():
    from sentence_search.deletion_service import SentenceDeletionService

    postgres = FakePostgresStore()
    service = SentenceDeletionService(
        FakeOpenSearchStore(),
        postgres,
        FakeSummaryService(),
    )

    result = service.delete_application("A1")

    assert postgres.deleted_summary_application == "A1"
    assert result["applicationSummariesDeleted"] == 1
    assert result["applicationSummariesRefreshed"] == 1
