from types import SimpleNamespace

from sentence_search.models import SentenceOccurrence
from sentence_search.sentence_service import SentenceService


class FakeOpenSearchStore:
    def __init__(self):
        self.catalog_documents = []
        self.occurrence_documents = []

    def existing_catalog_keys(self, keys):
        return set()

    def validate_occurrence_identity(self, records):
        return None

    def bulk_index_catalog(self, documents):
        self.catalog_documents.extend(documents)

    def bulk_index_occurrences(self, documents):
        self.occurrence_documents.extend(documents)

    def refresh_indices(self):
        return None


class FakePostgresStore:
    def register_sentence_keys(self, records):
        return {
            "registered": len(records),
            "newSentenceKeys": [records[0]["sentenceKey"]],
        }


class FakeMatchService:
    def match_sentence_key(self, sentence_key, threshold):
        raise AssertionError("Tracer sentences must not be matched")


class FakeSummaryService:
    def refresh_affected_applications(self, sentence_keys):
        raise AssertionError("Tracer sentences must not refresh summaries")


def test_tracer_gets_vector_but_does_not_get_matched():
    opensearch = FakeOpenSearchStore()
    service = SentenceService(
        SimpleNamespace(match_threshold=90, semantic_model_id="model-1"),
        opensearch,
        FakePostgresStore(),
        FakeMatchService(),
        FakeSummaryService(),
    )
    sentence = SentenceOccurrence.model_validate(
        {
            "applicationId": "A1",
            "tspId": "T1",
            "sectionName": "Statement",
            "globalId": "S1",
            "sentIdLocal": 1,
            "sentenceContent": "Standard tracer text",
            "isTracer": True,
            "isFormLanguage": False,
            "sourceType": "document",
            "analysisGroup": "Asylee",
        }
    )

    result = service.add_sentence(sentence)

    assert len(opensearch.catalog_documents) == 1
    assert len(opensearch.occurrence_documents) == 1
    assert result["catalogDocumentIndexed"] is True
    assert result["sentenceKeyRegistered"] is True
    assert result["matchCalculated"] is False
    assert result["status"] == "skipped"
