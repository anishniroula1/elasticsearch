from types import SimpleNamespace

from sentence_search.models import SentenceOccurrence
from sentence_search.sentence_service import SentenceService


class FakeOpenSearchStore:
    def __init__(self):
        self.catalog_documents = []
        self.occurrence_documents = []

    def existing_catalog_keys(self, keys):
        return set()

    def bulk_index_catalog(self, documents):
        self.catalog_documents.extend(documents)

    def bulk_index_occurrences(self, documents):
        self.occurrence_documents.extend(documents)

    def refresh_indices(self):
        return None


class FakePostgresStore:
    def validate_sentence_identity(self, records):
        return None

    def register_sentences(self, records, threshold):
        return {"registered": len(records), "jobsQueued": 0}


def test_tracer_gets_vector_but_does_not_get_match_job():
    opensearch = FakeOpenSearchStore()
    service = SentenceService(
        SimpleNamespace(match_threshold=90),
        opensearch,
        FakePostgresStore(),
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
    assert result["matchJobQueued"] is False
    assert result["status"] == "skipped"
