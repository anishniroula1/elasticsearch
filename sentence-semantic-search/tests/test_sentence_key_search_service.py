from math import sqrt
from types import SimpleNamespace

from sentence_search.sentence_key_search_service import SentenceKeySearchService

SOURCE_KEY = "a" * 64
SIMILAR_KEY = "b" * 64


class FakeOpenSearchStore:
    def __init__(self):
        self.search_keys = None

    def catalog_vectors(self, sentence_keys):
        assert sentence_keys == [SOURCE_KEY, SIMILAR_KEY]
        return {
            SOURCE_KEY: [1.0, 0.0],
            SIMILAR_KEY: [0.92, sqrt(1 - (0.92**2))],
        }

    def matching_occurrences(
        self,
        sentence_keys,
        excluded_application_id,
        analysis_group,
    ):
        self.search_keys = sentence_keys
        assert excluded_application_id == "A1"
        assert analysis_group == "Asylee"
        return [
            {"globalId": "S10", "sentenceKey": SOURCE_KEY},
            {"globalId": "S11", "sentenceKey": SIMILAR_KEY},
        ]


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

    result = service.search("A1", SOURCE_KEY, "Asylee")

    assert opensearch.search_keys == [SOURCE_KEY, SIMILAR_KEY]
    assert result["exactMatchCount"] == 1
    assert result["similarMatchCount"] == 1
    assert result["totalMatches"] == 2
    assert result["returnedMatches"] == 2
    assert result["matches"][0]["matchType"] == "exact"
    assert result["matches"][0]["matchPercentage"] == 100.0
    assert result["matches"][1]["matchType"] == "similar"
    assert result["matches"][1]["matchPercentage"] == 92.0
    assert "pagination" not in result
    assert "nextToken" not in result
    assert result["neuralSearchUsed"] is False
