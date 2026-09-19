from math import sqrt
from types import SimpleNamespace

from sentence_search.sentence_key_search_service import SentenceKeySearchService

SOURCE_KEY = "a" * 64
SIMILAR_KEY = "b" * 64
LOW_SCORE_KEY = "c" * 64


class FakeOpenSearchStore:
    def __init__(self):
        self.search_keys = None

    def catalog_vectors(self, sentence_keys):
        all_vectors = {
            SOURCE_KEY: [1.0, 0.0],
            SIMILAR_KEY: [0.92, sqrt(1 - (0.92**2))],
            LOW_SCORE_KEY: [0.83, sqrt(1 - (0.83**2))],
        }
        return {key: all_vectors[key] for key in sentence_keys}

    def catalog_vector(self, sentence_key):
        assert sentence_key == SOURCE_KEY
        return [1.0, 0.0]

    def catalog_matches(self, sentence_key, vector, threshold):
        assert sentence_key == SOURCE_KEY
        assert vector == [1.0, 0.0]
        assert threshold == 70
        return [
            {"sentenceKey": SOURCE_KEY},
            {"sentenceKey": SIMILAR_KEY},
            {"sentenceKey": LOW_SCORE_KEY},
        ]

    def matching_occurrences(
        self,
        sentence_keys,
        excluded_application_id,
        analysis_group,
    ):
        self.search_keys = sentence_keys
        assert excluded_application_id == "A1"
        assert analysis_group == "Asylee"
        occurrences = [
            {"globalId": "S10", "sentenceKey": SOURCE_KEY},
            {"globalId": "S11", "sentenceKey": SIMILAR_KEY},
            {"globalId": "S12", "sentenceKey": LOW_SCORE_KEY},
        ]
        return [
            occurrence
            for occurrence in occurrences
            if occurrence["sentenceKey"] in sentence_keys
        ]


class FakePostgresStore:
    def matching_keys(self, sentence_keys):
        assert sentence_keys == [SOURCE_KEY]
        return {
            SOURCE_KEY: {
                "matchingSentenceKeys": [SIMILAR_KEY, LOW_SCORE_KEY],
            }
        }


def test_sentence_key_search_uses_saved_keys_and_normal_filters():
    opensearch = FakeOpenSearchStore()
    service = SentenceKeySearchService(
        SimpleNamespace(match_threshold=90),
        opensearch,
        FakePostgresStore(),
    )

    result = service.search("A1", SOURCE_KEY, "Asylee", 90)

    assert opensearch.search_keys == [SOURCE_KEY, SIMILAR_KEY]
    assert result["exactMatchCount"] == 1
    assert result["similarMatchCount"] == 1
    assert result["totalMatches"] == 2
    assert result["returnedMatches"] == 2
    assert result["matches"][0]["matchType"] == "exact"
    assert result["matches"][0]["matchPercentage"] == 100.0
    assert result["matches"][1]["matchType"] == "similar"
    assert result["matches"][1]["matchPercentage"] == 92.0
    assert result["thresholdPercentage"] == 90
    assert result["directMatchingKeyCount"] == 1
    assert "pagination" not in result
    assert "nextToken" not in result
    assert result["neuralSearchUsed"] is False


def test_higher_query_threshold_removes_saved_lower_score_matches():
    opensearch = FakeOpenSearchStore()
    service = SentenceKeySearchService(
        SimpleNamespace(match_threshold=90),
        opensearch,
        FakePostgresStore(),
    )

    result = service.search("A1", SOURCE_KEY, "Asylee", 95)

    assert opensearch.search_keys == [SOURCE_KEY]
    assert result["thresholdPercentage"] == 95
    assert result["directMatchingKeyCount"] == 0
    assert result["exactMatchCount"] == 1
    assert result["similarMatchCount"] == 0


def test_lower_query_threshold_runs_live_catalog_search():
    opensearch = FakeOpenSearchStore()
    service = SentenceKeySearchService(
        SimpleNamespace(match_threshold=90),
        opensearch,
        FakePostgresStore(),
    )

    result = service.search("A1", SOURCE_KEY, "Asylee", 70)

    assert opensearch.search_keys == [SOURCE_KEY, SIMILAR_KEY, LOW_SCORE_KEY]
    assert result["thresholdPercentage"] == 70
    assert result["directMatchingKeyCount"] == 2
    assert result["similarMatchCount"] == 2
    assert {match["matchPercentage"] for match in result["matches"]} == {
        100.0,
        92.0,
        83.0,
    }
