from types import SimpleNamespace

import pytest

from sentence_search.search_utils import encode_page_token
from sentence_search.sentence_key_search_service import SentenceKeySearchService

SOURCE_KEY = "a" * 64
SIMILAR_KEY = "b" * 64


class FakeOpenSearchStore:
    def __init__(self):
        self.page_keys = None

    def occurrence_counts_by_key(
        self,
        sentence_keys,
        excluded_application_id,
        analysis_group,
    ):
        assert sentence_keys == [SOURCE_KEY, SIMILAR_KEY]
        assert excluded_application_id == "A1"
        assert analysis_group == "Asylee"
        return {SOURCE_KEY: 2, SIMILAR_KEY: 3}

    def matching_occurrence_page(
        self,
        sentence_keys,
        excluded_application_id,
        analysis_group,
        page_size,
        after_global_id,
        include_total,
    ):
        self.page_keys = sentence_keys
        assert include_total is False
        return {
            "matches": [
                {"globalId": "S10", "sentenceKey": SOURCE_KEY},
                {"globalId": "S11", "sentenceKey": SIMILAR_KEY},
            ],
            "nextAfterGlobalId": None,
            "totalMatches": None,
        }


class FakePostgresStore:
    def matching_keys(self, sentence_keys):
        assert sentence_keys == [SOURCE_KEY]
        return {
            SOURCE_KEY: {
                "matchingSentenceKeys": [SIMILAR_KEY],
            }
        }


def test_sentence_key_search_uses_saved_keys_and_normal_filters():
    opensearch = FakeOpenSearchStore()
    service = SentenceKeySearchService(
        SimpleNamespace(match_threshold=90),
        opensearch,
        FakePostgresStore(),
    )

    result = service.search("A1", SOURCE_KEY, "Asylee", 100, None)

    assert opensearch.page_keys == [SOURCE_KEY, SIMILAR_KEY]
    assert result["exactMatchCount"] == 2
    assert result["similarMatchCount"] == 3
    assert result["totalMatches"] == 5
    assert result["matches"][0]["matchType"] == "exact"
    assert result["matches"][1]["matchType"] == "semantic"
    assert result["neuralSearchUsed"] is False


def test_search_token_stops_when_the_saved_key_list_changes():
    service = SentenceKeySearchService(
        SimpleNamespace(match_threshold=90),
        FakeOpenSearchStore(),
        FakePostgresStore(),
    )
    token = encode_page_token(
        {
            "type": "sentenceKeySearch",
            "applicationId": "A1",
            "analysisGroup": "Asylee",
            "sentenceKey": SOURCE_KEY,
            "pageSize": 100,
            "afterGlobalId": "S9",
            "exactMatchCount": 2,
            "similarMatchCount": 3,
            "returnedMatches": 2,
            "keyFingerprint": "old-key-list",
        }
    )

    with pytest.raises(ValueError, match="does not belong"):
        service.search("A1", SOURCE_KEY, "Asylee", 100, token)
