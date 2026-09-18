import pytest
from pydantic import ValidationError

from sentence_search.models import SentenceOccurrence
from sentence_search.text import sentence_key


def sentence_values():
    return {
        "applicationId": "A0001",
        "tspId": "TSP-1",
        "sectionName": "Written Statement",
        "globalId": "SENT-1",
        "sentIdLocal": 1,
        "sentenceContent": "The United States Government issued a notice.",
        "isTracer": False,
        "isFormLanguage": False,
        "sentenceKey": None,
        "sourceType": "document",
        "createdAt": None,
        "updatedAt": None,
        "analysisGroup": "group-1",
    }


def test_sentence_generates_key_and_dates():
    sentence = SentenceOccurrence.model_validate(sentence_values())
    assert sentence.sentenceKey == sentence_key(sentence.sentenceContent)
    assert sentence.documentId == sentence.tspId
    assert sentence.createdAt is not None
    assert sentence.updatedAt is not None
    assert "sentIdLocal" in sentence.model_dump()
    assert "local_global_id" not in sentence.model_dump()


def test_wrong_supplied_key_is_rejected():
    values = sentence_values()
    values["sentenceKey"] = "wrong"
    with pytest.raises(ValidationError):
        SentenceOccurrence.model_validate(values)
